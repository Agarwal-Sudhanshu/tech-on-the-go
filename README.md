# Tech On The Go

Your 5-minute tech catch-up. Global Technology • AI • Startups • Innovation.

A fully automated daily tech briefing, published as a YouTube video and a podcast. Every morning a GitHub Actions job:

1. **Collects** the last day's news from about 50 public feeds (`pipeline/collect.py`)
2. **Writes** a short spoken script with Claude, using only facts from those sources (`pipeline/write_script.py`)
3. **Narrates** it in the host's own cloned voice with an open-source model that runs on the job's CPU (`voice/speak.py`)
4. **Renders** a 1080p video with headline slides, captions and a waveform (`video/render.py`)
5. **Publishes** to YouTube (`publish/youtube_upload.py`) and to a podcast feed on GitHub Pages (`publish/podcast.py`)

No servers and no paid services: it runs on GitHub's free runners.

## Layout

| Path | What it is |
|---|---|
| `config/channel.json` | Channel name, episode length, publishing settings |
| `config/feeds.json` | News sources and topics |
| `run_episode.sh` | Builds one episode end to end |
| `.github/workflows/daily.yml` | The daily schedule |
| `episodes/` | The script of every published episode |
| `docs/` | One-time setup notes for YouTube and podcast platforms |

## Voice privacy

The voice reference clip is stored encrypted (`voice/ref.wav.gpg`); the key lives only in the repository's secrets. The narration is an AI-generated clone of the host's own voice, and the scripts are AI-assisted.

## Run locally

```sh
docker build -t briefstudio voice
docker run --rm -v "$PWD:/proj" --entrypoint sh briefstudio run_episode.sh
```
