# Audio Transcription Pipeline

A small script that takes an audio file (WAV or MP3) and returns a transcript as JSON, including the detected language, the duration, the full text, and start and end timestamps for every segment.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python transcribe.py sample.mp3
```

Options:

```bash
.venv/bin/python transcribe.py sample.wav --model small -o result.json
```

Example output:

```json
{
  "file": "sample.mp3",
  "language": "en",
  "duration_s": 4.41,
  "text": "The quarterly report is due on Friday, please send the revised budget to Sarah.",
  "segments": [
    { "start": 0.0, "end": 4.2, "text": "The quarterly report is due on Friday, please send the revised budget to Sarah." }
  ]
}
```

The first run downloads the model weights (about 140 MB for `base`), so it needs internet once.

## Approach

The task is about engineering decisions, not model training, so I used an existing open-source speech-to-text model and spent the effort on the parts around it: input handling, output shape, and how this would run at scale.

I kept the core as one function, `transcribe(path, model_size)`, that returns a plain dictionary. The command line wrapper just calls it and prints JSON. This makes it easy to import into a web service later without changing anything.

## Key decisions

**faster-whisper instead of OpenAI's whisper or a cloud API.**
It runs the same Whisper models locally, but it is several times faster on CPU and uses less memory because it runs on CTranslate2 with int8 quantization. Running locally means no API key, no cost per minute, and no audio leaving the machine, which matters for privacy.

**No separate audio conversion step.**
faster-whisper decodes audio through PyAV, which bundles FFmpeg libraries. WAV, MP3 and other common formats work directly, and the audio is resampled to 16 kHz mono internally. That removes a system dependency and a subprocess call.

**Extension allowlist and early errors.**
The script checks that the file exists and has a supported extension (`.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`) before loading the model. Loading the model is the slow part, so bad input should fail in milliseconds with a clear message, not after several seconds with a stack trace.

**Voice activity detection on.**
`vad_filter=True` skips silent stretches. This saves compute on long recordings and also reduces a known Whisper problem where it invents text during silence.

**Timestamps per segment as the main output.**
Segments are the natural unit Whisper produces, and they are what downstream tools need (subtitles, search, jumping to a point in the audio). Word level timestamps are available in the library but cost more time, and the task only asked for segments.

**JSON output.**
It is easy to read, easy to pipe into other programs, and easy to return from an API.

**Default model is `base`.**
It is small and fast enough to test quickly on a laptop. Accuracy improves with `small`, `medium` or `large-v3` at the cost of speed. It is a command line flag so the trade-off is the caller's choice.

## Code structure

```
transcribe.py     the whole implementation (about 45 lines)
requirements.txt  faster-whisper
sample.wav        short generated test clip
sample.mp3        same clip as MP3
```

Inside `transcribe.py`:

- `transcribe()` validates the path and extension, loads the model, runs transcription, and builds the result dictionary.
- The `__main__` block parses arguments, turns known errors into a clean `error: ...` message and exit code, and writes JSON to stdout or to a file.

## Handling long audio

faster-whisper processes audio in 30 second windows and yields segments as it goes, so long files work without manual chunking. Memory use is mainly the decoded audio, roughly 230 MB per hour. I have only tested short clips, so I have not measured real long-file performance.

Known limits for very long files, and what I would do about them:

- Everything is collected before printing, so a crash late in a run loses all output. Fix: write segments to disk as they are produced.
- Whisper conditions each window on the previous text, which can cause repetition loops on long audio. Fix: set `condition_on_previous_text=False`.
- There is no progress output. Fix: log each segment as it arrives.
- For very large files, split on silence with FFmpeg and transcribe the pieces in parallel.

## System design (how this would run as a service)

I did not build this part, but this is how I would do it.

**Concurrent uploads.** Transcription is slow and CPU or GPU bound, so it must not run inside the upload request. The API validates the file, stores it in object storage, creates a job record, puts a message on a queue (SQS or Redis), and returns `202 Accepted` with a job id. A separate pool of workers pulls jobs, each with the model loaded once, and writes results. Workers scale on queue depth, and bursts make the queue longer instead of dropping requests. I would add file size and duration limits, per client rate limits, and per tenant fairness so one client cannot starve the rest.

**Retries and recovery.** Transient failures (worker crash, out of memory, network issue) are retried with exponential backoff and jitter, up to about three attempts. Permanent failures (corrupt audio, unsupported format) are marked failed straight away because retrying cannot help. Workers acknowledge a message only after the result is saved, so a crashed worker's job returns to the queue. Because a job can run twice, results are written under the job id so duplicates overwrite. Jobs that run out of retries go to a dead letter queue with the error, where someone can inspect and replay them. A periodic sweeper requeues jobs stuck in `processing`. For long files, progress is saved per chunk so a retry resumes instead of restarting.

**API shape.** An asynchronous REST API with a job resource:

- `POST /v1/transcriptions` takes the audio and optional `language`, `model` and `callback_url`, and returns `202` with a job id.
- `GET /v1/transcriptions/{id}` returns the status and, when done, the transcript with segments. `?format=srt` or `vtt` can return subtitles.
- `DELETE /v1/transcriptions/{id}` removes the job and its audio.
- An `Idempotency-Key` header prevents duplicate jobs from client retries.
- Standard errors: `400` bad input, `413` too large, `415` unsupported format, `429` rate limited, `404` unknown job.

## Assumptions and trade-offs

- Speech is clear enough for Whisper. Heavy noise, overlapping speakers and strong accents lower accuracy, and there is no speaker identification.
- Language is auto-detected from the start of the audio. Mixed language recordings may be handled poorly. A `--language` flag would fix this and is a small addition.
- The model is reloaded on every call. That is fine for a one-off script but wasteful for a service, where it should be loaded once per worker.
- CPU with int8 was chosen so it runs on any machine. A GPU would be much faster.
- The extension check is only a first filter. A mislabeled or corrupt file passes it and fails inside the decoder with a raw error. A friendlier "could not decode audio" message would be a good improvement.
- Tested only on a short generated MP3 (text to speech), which is cleaner than real recordings. There are no automated tests yet.
- The task mentions processing the result for downstream use. I returned structured JSON with timestamps as that handoff, and did not add summarization or keyword extraction.
