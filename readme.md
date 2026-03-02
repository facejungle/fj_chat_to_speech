# FJ Chat to Speech

<img alt="FJ Chat to Speech" src="./docs/app_0.png" width="700">

FJ Chat to Speech is an open-source desktop application that converts live chat messages from YouTube and Twitch streams into real-time speech.

- YouTube and Twitch live chat support
- Local text-to-speech with `Silero`
- Local toxicity filtering with `Detoxify`
- English and Russian voices
- Adjustable speech speed and volume
- Number pronunciation support
- Optional message translation before speech
- Stop-word list editor
- Spam filters (symbol spam, links)
- List of banned
- English and Russian UI
- Free and open-source

## <a href="https://github.com/facejungle/fj_chat_to_speech/releases/latest/" target="_blank">Download</a>

- `F11` - Expand/collapse chat to full window
- `Arrow Left` / `Arrow Right` - Speech rate
- `Arrow Up` / `Arrow Down` - Volume
- `Space` - Pause/Play

## How is the banned list formed?

Each toxic message is assigned a score. For example, if a message contains stop-words, it receives a score of `0.25`. The message is also processed through the Detoxify model, which assigns a toxicity level ranging from `0.0` to `1.0`. However, messages with a toxicity level below the value specified in the `Toxicity threshold` parameter are ignored.

During the application's operation, statistics are accumulated for toxic users. If a user exceeds the value specified in the `Toxicity level for user ban` parameter, they are added to the banned list.

- Messages from banned users will not be voiced.
- Chat moderators and the channel owner cannot be banned.

## Run from source

```bash
git clone https://github.com/facejungle/fj_chat_to_speech.git
cd fj_chat_to_speech

pip install -r torch.requirements.txt
pip install -r requirements.txt

python main.py
```

Notes:

- On first run, model-related files downloaded and cached.
- The settings file is located in:
  - Windows: `~\AppData\Roaming\FJ Chat to Speech`
  - Linux or MacOS: `~/.fj_chat_to_speech`

## Connect to YouTube

1. Paste a live stream URL (or video ID) into the YouTube field.
2. Click `Connect`.

## Connect to Twitch

1. Create a Twitch application and copy its `Client ID`.
   - <a href="https://github.com/facejungle/fj_chat_to_speech/wiki/Twitch-CLIENT-ID" target="_blank">How to create Client ID</a>
2. Open Twitch configure in the app paste your `Client ID` and click `Save`.
3. Confirm authorization in your browser (Device Code flow).
4. Enter an active channel name or Twitch URL.
5. Click `Connect`.

The app stores access/refresh tokens in local settings and refreshes access tokens automatically when needed.

## Troubleshooting

- Twitch connection fails:
  - Confirm the `Client ID` is valid.
  - Re-run Twitch authorization in app settings.
  - Ensure the channel is live and the name/URL is correct.
- YouTube connection fails:
  - Check that the stream is currently live.
  - Try pasting a direct video URL instead of a shortened link.

## Build

```bash
git clone https://github.com/facejungle/fj_chat_to_speech.git
cd fj_chat_to_speech

python build.py
```

Build artifacts are created in `dist/`.

## Thanks for <a href="https://github.com/snakers4/silero-models/" target="_blank">Silero</a> and <a href="https://github.com/unitaryai/detoxify" target="_blank">Detoxify</a>

- **Silero**: We use Silero’s open-source TTS models, known for natural-sounding voices and fast CPU performance
- **Detoxify**: Toxicity filtering is powered by the Detoxify library, which provides pretrained models to flag and filter toxic comments
