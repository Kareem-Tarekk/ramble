# Ramble

Hold a key, talk through your thoughts, and get organized text typed where your cursor is: a clean AI prompt in ChatGPT or Claude, an email in Outlook, slide bullets in PowerPoint. Free to run on Groq's free plan.

Windows only.

## Set up

1. Install [Python 3.13](https://www.python.org/downloads/) (the installer adds the `py` launcher).
2. Download this repository (Code > Download ZIP, or `git clone`).
3. Create a free Groq API key: [console.groq.com](https://console.groq.com) > API Keys > Create API Key.
4. Double-click **Start Ramble.bat**. The first run sets everything up (a one-time download of about 30 MB; Ramble fetches a 27 MB audio compressor by itself in the background right after, which only makes uploads faster), then asks for your Groq key. A purple mic appears in the tray next to the clock (click the **^** arrow if it's hidden).

## Use it

| Do this | What happens |
| --- | --- |
| Hold **Right Ctrl**, talk, let go | Organized text is typed where your cursor is |
| Double-tap **Right Ctrl** | Hands-free; tap once more to finish |
| Tap **Shift** while recording | Change the mode (Edit comes first when text is selected; in AI apps, Follow-up comes first) |
| Talk again within 5 seconds of a paste | Adds to it: the old paste is undone and the whole thing is rewritten as one (pill shows "Continuing…") |
| **Esc** while recording | Cancel |
| **Ctrl+Alt+L** | Paste the last result again |
| Start with "As an email: ..." or "Follow up: ..." | Use that mode (email, prompt, follow up, message, slides, clean) |
| Say "new line", "new paragraph", "bullet point" | Formatting |

## Modes

Picked from the app you're in:

| Where you're typing | Mode |
| --- | --- |
| Outlook, Gmail | Email: greeting, short paragraphs, a clear ask, sign-off; replies to the thread in the box |
| PowerPoint, Google Slides | Slides: short parallel bullets that match the ones already there |
| Slack, Teams, WhatsApp, Discord | Message: short and casual |
| ChatGPT, Claude, Gemini, Cursor, VS Code, terminals | AI prompt: a short request (25 words or fewer) stays one or two sentences; a longer one is laid out as Goal, Context, Requirements, Constraints, Expected output, Open points (empty sections left out). Say "start over" for one complete prompt for the whole task |
| Shift once while recording in an AI app | Follow-up: for a chat that already has the task. The change first, then why and what to keep, 1 to 5 lines, no labels |
| Anything else | Clean: removes fillers and fixes grammar, keeps your wording |
| Text selected, then Shift while recording | Edit: say the change ("shorter", "more formal", "turn into bullets") |

Tray > **Open modes** to change which app gets which mode, the default mode, and what each mode tells the AI. Changes apply on the next dictation.

The AI prompt mode follows common prompting guidance from Anthropic, OpenAI and Google: outcome first, specific, every point you made kept exactly once, nothing invented, copied errors and code kept word for word.

Follow-up only uses what you say and text you copied in the 3 seconds before talking. It never reads earlier dictations or the chat, which may be days old or started without Ramble. Follow-ups lead with the change because models do worse when instructions are spread vaguely over many turns (Laban et al., "LLMs Get Lost In Multi-Turn Conversation", 2025). After two failed corrections, starting a new chat with one complete prompt usually works better ("start over").

Both modes check the result: names, numbers, paths and code identifiers that appear nowhere in what you said or copied are sent back to the AI once. If any remain, the pill lists them so you can check.

## Customize the instructions

Everything the AI is told lives in `modes.json` (tray > **Open modes**, or `%USERPROFILE%\.ramble\modes.json`). Edit it in any text editor and save; the next dictation uses it, no restart needed. If the file has a typo, Ramble keeps the old version and shows a warning.

**Change what a mode does:** each mode has a list of `instructions`, one rule per line. Add, remove or reword lines:

```json
"email": {
  "label": "Email",
  "context": ["field", "clipboard"],
  "instructions": [
    "Write the email body: a greeting, short paragraphs, one clear ask, and a sign-off.",
    "Always sound warm but brief.",
    "Never use exclamation marks."
  ]
}
```

- `label`: the name shown on the status pill.
- `context`: what the AI may see besides your words: `"field"` (text around your cursor) and `"clipboard"` (text you copied in the last 3 seconds). Use `[]` for neither.
- `instructions`: the rules. Short, specific lines work best.
- `examples` (optional): one or two examples of the format you want, each as `"Spoken: ...\nOutput:\n..."`. Keep them consistent with each other and with the rules.
- `required` (optional): text the reply must contain, e.g. `"Goal:"` in AI prompt mode. If it's missing, Ramble asks the AI once more.
- `check_details` (optional): `true` sends back names and numbers you didn't say (see above).
- `short` (optional, AI prompt): the `instructions`, `examples` and `required` used instead when you say `max_words` words or fewer.

**Add your own mode:** add a new entry under `modes`, e.g. a mode for your team's bug reports:

```json
"bug": {
  "label": "Bug report",
  "context": ["clipboard"],
  "instructions": [
    "Turn what they said into a bug report with these labels: Summary, Steps to reproduce, Expected, Actual.",
    "Put any copied error message under Actual, word for word."
  ]
}
```

It then shows up when you tap Shift while recording, and under tray > Redo last as (after a restart).

**Pick the mode per app:** in `app_rules`, list an app's process name in lowercase under `apps` (find it in Task Manager > Details, e.g. `notion.exe`), or lowercase words from a browser tab's title under `browser_titles`. Rules are checked top to bottom; anything unmatched gets `default_mode`.

```json
{"mode": "bug", "apps": ["jira.exe"], "browser_titles": ["jira", "linear"]}
```

**Instructions for every mode:** the rules that apply everywhere (output only the text, never invent facts, remove fillers, keep the speaker's language) are in `OUTPUT_RULES` and `DICTATION_RULES` near the top of `ramble.py`.

**Start over:** delete `modes.json` and restart Ramble to get the defaults back.

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
| `continue_seconds` | `5` | Talk again this soon after a paste to add to it; `0` turns it off. Up to 3 recordings or 90 seconds are joined. If you pressed Enter in between, the new part becomes a follow-up instead |

## Limits and stats

- **Limit meter:** the tray shows today's dictations and how much of Groq's daily limit is used (`daily_token_limit`, 200K tokens on the free plan). You get a warning at 80%.
- **Auto fallback:** when `openai/gpt-oss-120b` runs out for the day, Ramble switches to `openai/gpt-oss-20b`, which has its own daily limit.
- **Tray > Usage stats:** dictations, minutes spoken, words written, time saved compared with typing, and the modes you used.

## When something goes wrong

- **No internet, or Groq's limit is used up:** the AI step is skipped and your words are lightly cleaned and pasted. Speech-to-text also needs Groq unless you install **offline mode**: double-click **Install offline mode.bat** once (about 500 MB, mostly the Whisper speech model), and Ramble then transcribes on your PC when Groq can't be reached. Without it, you'll see a message saying so.
- **No text box selected:** the text is copied. Press Ctrl+V.
- **Password fields:** Ramble never reads or types into them.
- **Want the last result again or in another mode:** tray > Copy last result, or Redo last as. `history.jsonl` keeps the last 50 dictations, including exactly what was sent to the AI.

## Privacy and files

Your audio, the transcript, the app name and window title, nearby text in the box you're typing in, and anything you copied in the last few seconds are sent to Groq. Your key, settings, modes, history, usage stats, log and Ramble's Python environment stay on your PC in `%USERPROFILE%\.ramble`; nothing personal is stored in this folder.

## License

MIT. See [LICENSE](LICENSE).
