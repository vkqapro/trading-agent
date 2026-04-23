# Trading Agent with OpenAI Codex

This project demonstrates how to connect to OpenAI's Codex model for code generation, which can be useful in a trading agent for generating trading strategies, data analysis code, etc.

## Prerequisites

- Python 3.7+
- OpenAI API key

## Setup

1. Clone or download this repository.

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Get your OpenAI API key from [OpenAI Platform](https://platform.openai.com/).

4. Set the API key as an environment variable:
   - On Windows: `set OPENAI_API_KEY=your_api_key_here`
   - Or create a `.env` file in the project root with `OPENAI_API_KEY=your_api_key_here` and install `python-dotenv` to load it.

## Usage

Run the main script:
```
python main.py
```

This will send a sample prompt to Codex and print the response.

## Customization

Modify the `prompt` variable in `main.py` to send different queries to Codex.

## Troubleshooting

- Ensure your API key is valid and has credits.
- Codex models may have usage limits; check OpenAI's documentation.
- If you encounter rate limits, implement retries or use a different model.

## Note

OpenAI has deprecated some Codex models in favor of GPT-3.5 and GPT-4, but `code-davinci-002` is still available. For more advanced code generation, consider using GPT-4.