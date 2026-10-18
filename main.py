import os
import sys
import time
import signal
import subprocess
import threading
from datetime import datetime

# ================== SETTINGS ==================

TIKTOK_URL = os.getenv(
    "TIKTOK_URL",
    "https://www.tiktok.com/@yousryyakout/live"
).strip()

STREAM_KEY = os.getenv("YOUTUBE_STREAM_KEY", "9gs2-gayu-3pjh-wda0-1tp7").strip()
DEFAULT_RTMP = (
    f"rtmp://a.rtmp.youtube.com/live2/{STREAM_KEY}" if STREAM_KEY else ""
)
YOUTUBE_RTMP = os.getenv("YOUTUBE_RTMP", DEFAULT_RTMP).strip()

CHECK_INTERVAL_OFFLINE = 15
RETRY_DELAY = 30
MIN_GOOD_SESSION = 60

stop_event = threading.Event()
streamlink_process = None
ffmpeg_process = None


# ================== LOGGING ==================

def log(message):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}",
        flush=True
    )


# ================== PROCESS MANAGEMENT ==================

def kill_process_tree(process):
    if process is None or process.poll() is not None:
        return

    try:
        pgid = os.getpgid(process.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            process.wait(timeout=2)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def cleanup():
    global streamlink_process, ffmpeg_process

    if ffmpeg_process:
        kill_process_tree(ffmpeg_process)
        ffmpeg_process = None

    if streamlink_process:
        kill_process_tree(streamlink_process)
        streamlink_process = None


def signal_handler(sig, frame):
    log("Shutdown signal received. Cleaning up...")
    stop_event.set()
    cleanup()


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ================== STREAM CHECK ==================

def is_tiktok_live_online():
    cmd = ["streamlink", "--stream-url", TIKTOK_URL, "best"]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15
        )
        if res.returncode == 0 and res.stdout.strip().startswith("http"):
            return True
    except Exception:
        pass
    return False


# ================== COMMAND BUILDERS ==================

def make_streamlink_command():
    return [
        "streamlink",
        "--loglevel", "warning",
        "--hls-live-edge", "4",
        "--ringbuffer-size", "128M",
        "--retry-streams", "5",
        "--retry-max", "10",
        "--stream-segment-attempts", "5",
        "--stream-segment-timeout", "10",
        "--stream-timeout", "20",
        "--stdout",
        TIKTOK_URL,
        "best"
    ]


def make_ffmpeg_command():
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-stats_period", "10",
        "-nostdin",
        "-fflags", "+genpts+discardcorrupt",
        "-analyzeduration", "10M",
        "-probesize", "10M",
        "-thread_queue_size", "2048",
        "-i", "pipe:0",
        "-map", "0:v:0",
        "-c:v", "copy",
        "-map", "0:a:0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-af", "aresample=async=1",
        "-flvflags", "no_duration_filesize",
        "-f", "flv",
        YOUTUBE_RTMP
    ]


# ================== START STREAM ==================

def start_stream():
    global streamlink_process, ffmpeg_process

    if stop_event.is_set():
        return False

    log("Checking if TikTok Live is online...")
    if not is_tiktok_live_online():
        log("TikTok Live is offline or stream is not ready.")
        return False

    log("Connecting to TikTok Live via Streamlink...")

    streamlink_process = subprocess.Popen(
        make_streamlink_command(),
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=0,
        start_new_session=True
    )

    time.sleep(4)
    if streamlink_process.poll() is not None:
        log("Streamlink exited. TikTok may be offline.")
        return False

    if stop_event.is_set():
        return False

    log("Starting FFmpeg -> YouTube RTMP...")

    ffmpeg_process = subprocess.Popen(
        make_ffmpeg_command(),
        stdin=streamlink_process.stdout,
        stdout=subprocess.DEVNULL,
        stderr=None,
        bufsize=0,
        start_new_session=True
    )

    if streamlink_process.stdout:
        streamlink_process.stdout.close()

    log("Relay started. Video: COPY (no re-encode); Audio: AAC 128k.")

    while not stop_event.is_set():
        sl_code = streamlink_process.poll()
        ff_code = ffmpeg_process.poll()

        if sl_code is not None:
            log(f"Streamlink stopped (exit code {sl_code}).")
            return False

        if ff_code is not None:
            log(f"FFmpeg stopped (exit code {ff_code}).")
            return False

        time.sleep(1)

    return False


# ================== MAIN LOOP ==================

def main():
    if not TIKTOK_URL:
        log("ERROR: TIKTOK_URL is empty.")
        sys.exit(1)

    if not YOUTUBE_RTMP:
        log("ERROR: YOUTUBE_RTMP is empty.")
        sys.exit(1)

    log("TikTok -> YouTube Relay Started")
    log("Video: stream copy (no re-encode)")
    log("Audio: re-encoded to AAC 128k")
    log("Automatic reconnection: ON")

    while not stop_event.is_set():
        started_at = time.monotonic()

        try:
            cleanup()
            start_stream()
        except Exception as exc:
            log(f"Unexpected error: {exc}")
        finally:
            cleanup()

        if stop_event.is_set():
            break

        elapsed = time.monotonic() - started_at

        if elapsed > MIN_GOOD_SESSION:
            delay = RETRY_DELAY
            log(f"Stream disconnected. Waiting {delay}s so YouTube closes the old session...")
        else:
            delay = CHECK_INTERVAL_OFFLINE
            log(f"Stream unavailable. Checking again in {delay}s...")

        stop_event.wait(delay)

    cleanup()
    log("Relay stopped cleanly.")


if __name__ == "__main__":
    main()
