import openai
import os

# Set your OpenAI API key
# You can set it as an environment variable or hardcode it (not recommended for production)
openai.api_key = os.getenv("OPENAI_API_KEY")

def connect_to_codex(prompt):
    """
    Function to send a prompt to OpenAI's Codex model and get a response.
    """
    try:
        response = openai.Completion.create(
            engine="code-davinci-002",  # Codex model
            prompt=prompt,
            max_tokens=150,
            n=1,
            stop=None,
            temperature=0.5,
        )
        return response.choices[0].text.strip()
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    # Example prompt for a trading agent
    prompt = "Write a Python function to calculate the moving average of a list of stock prices."
    result = connect_to_codex(prompt)
    print("Codex Response:")
    print(result)