# Ramble

Hold a key, talk through your thoughts, and get organized text typed where your cursor is: a clean AI prompt in ChatGPT or Claude, an email in Outlook, slide bullets in PowerPoint. Free to run on Groq's free plan.

Windows only.

## Set up

1. Install [Python 3.13](https://www.python.org/downloads/) (the installer adds the `py` launcher).
2. Download this repository (Code > Download ZIP, or `git clone`).
3. Create a free Groq API key: [console.groq.com](https://console.groq.com) > API Keys > Create API Key.
4. Double-click **Start Ramble.bat**. The first run sets everything up (a few minutes), then asks for your Groq key. A purple mic appears in the tray next to the clock (click the **^** arrow if it's hidden).

## Use it

| Do this | What happens |
| --- | --- |
| Hold **Right Ctrl**, talk, let go | Organized text is typed where your cursor is |
| Double-tap **Right Ctrl** | Hands-free; tap once more to finish |
| Tap **Shift** while recording | Change the mode (Edit comes first when text is selected) |
| **Esc** while recording | Cancel |
| **Ctrl+Alt+L** | Paste the last result again |
| Start with "As an email: ..." | Use that mode (email, prompt, message, slides, clean) |
| Say "new line", "new paragraph", "bullet point" | Formatting |

## Modes

Picked from the app you're in:

| Where you're typing | Mode |
| --- | --- |
| Outlook, Gmail | Email: greeting, short paragraphs, a clear ask, sign-off; replies to the thread in the box |
| PowerPoint, Google Slides | Slides: short parallel bullets that match the ones already there |
| Slack, Teams, WhatsApp, Discord | Message: short and casual |
| ChatGPT, Claude, Gemini, Cursor, VS Code, terminals | AI prompt, always laid out as Goal, Context, Requirements, Constraints, Expected output, Open points (empty sections left out) |
| Anything else | Clean: removes fillers and fixes grammar, keeps your wording |
| Text selected, then Shift while recording | Edit: say the change ("shorter", "more formal", "turn into bullets") |

Tray > **Open modes** to change which app gets which mode, the default mode, and what each mode tells the AI. Changes apply on the next dictation.

The AI prompt mode follows common prompting guidance from Anthropic, OpenAI and Google: outcome first, specific, every point you made kept exactly once, nothing invented, copied errors and code kept word for word.

## Settings

Tray > **Open settings**. Most changes apply on the next dictation; hotkeys and the microphone need a restart (tray > Quit, then start again).

| Setting | Default | Notes |
| --- | --- | --- |
| `dictionary` | `[]` | Names and terms to spell right, e.g. `["Priya", "Kubernetes"]` |
| `snippets` | `{}` | Spoken trigger to text, e.g. `{"my sign-off": "Best regards,\nAlex"}` |
| `hotkey`, `mode_key`, `paste_last_hotkey` | `ctrl_r`, `shift`, `ctrl+alt+l` | Other keys: `alt_r`, `f9`, `caps_lock`, `scroll_lock` |
| `llm_model` | `openai/gpt-oss-120b` | `openai/gpt-oss-20b` is faster |
| `stt_model` | `whisper-large-v3-turbo` | `whisper-large-v3` is more accurate, especially for non-English speech |
| `language` | `null` (detect) | e.g. `"en"` or `"ar"` to force one |
| `output_language` | `same` | Also on the tray: `english`, `franco` (Franco-Arabic), `arabic`, `french` |
| `keep_mic_warm` | `true` | Recording starts instantly; Windows shows the mic as in use |
| `sounds` | `true` | Soft start and stop blips |
| `llm_timeout_seconds` | `5` | After this, the plainly cleaned transcript is inserted instead |

## Limits and stats

- **Limit meter:** the tray shows today's dictations and how much of Groq's daily limit is used (`daily_token_limit`, 200K tokens on the free plan). You get a warning at 80%.
- **Auto fallback:** when `openai/gpt-oss-120b` runs out for the day, Ramble switches to `openai/gpt-oss-20b`, which has its own daily limit.
- **Tray > Usage stats:** dictations, minutes spoken, words written, time saved compared with typing, and the modes you used.

## When something goes wrong

- **No internet or Groq's limit is used up:** speech is turned into text on your PC (Whisper, downloaded on first use) and lightly cleaned.
- **No text box selected:** the text is copied. Press Ctrl+V.
- **Password fields:** Ramble never reads or types into them.
- **Want the last result again or in another mode:** tray > Copy last result, or Redo last as. `history.jsonl` keeps the last 50 dictations, including exactly what was sent to the AI.

## Privacy and files

Your audio, the transcript, the app name and window title, nearby text in the box you're typing in, and anything you copied in the last few seconds are sent to Groq. Your key, settings, modes, history, usage stats, log and Ramble's Python environment stay on your PC in `%USERPROFILE%\.ramble`; nothing personal is stored in this folder.

## License

MIT. See [LICENSE](LICENSE).
