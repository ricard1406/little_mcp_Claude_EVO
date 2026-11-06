# Disclaimer

## Sample data

**All data in this project is sample data, for testing purposes only.**

- **Candidates list** (`data/Candidates and Scores List - Test Data - compact.pdf`):
  every name, location and score is fictional and AI generated. Any
  resemblance to real persons, living or dead, or to actual places is purely
  coincidental.
- **Warehouse inventory** (the FRUITS and VEGGIE tables): the items and
  quantities are made up.

The demo shows how an AI agent can search documents and work with a
database. The data does not describe anyone or anything real.

## AI answers can be wrong

This is a learning project. The answers come from an AI model, which can
misunderstand a question, pick the wrong tool, or state something false with
confidence. Check what it tells you, and check anything it writes for you
(such as an interview invitation) before you use or send it. Don't rely on it
for real business, legal, financial or hiring decisions.

## Where your data goes

What leaves your computer depends on the options you use:

| You use | What is sent, and to whom |
|---|---|
| Ollama (`--provider ollama`, the default) | Your questions and documents stay on your computer. |
| Claude (`--provider anthropic`) | Your questions, the conversation, and the tool results are sent to Anthropic. Tool results include passages from your documents and inventory data. This is covered by [Anthropic's terms](https://www.anthropic.com/legal). |
| The weather tool | The city name is sent to [OpenWeather](https://openweathermap.org). |
| The time tool, for a city | The city name is sent to [OpenStreetMap Nominatim](https://nominatim.org), to find its time zone. |

If you add your own documents to `data/` and use Claude, passages from them
can be sent to Anthropic. **Don't put personal or confidential documents
there unless you are allowed to share them in this way.**

## Third-party software and models

`install.sh` asks before downloading anything. If you accept, it downloads
software from Ubuntu's package archive, Python libraries from PyPI,
[Ollama](https://ollama.com), and AI models through Ollama. Each one has its
own license and terms.

This project is not affiliated with or endorsed by Anthropic, Ollama,
OpenWeather or OpenStreetMap. "Claude" is a trademark of Anthropic.

## No warranty

The software is provided "as is", without any warranty, under the
[MIT License](LICENSE). The author accepts no responsibility for its use.
