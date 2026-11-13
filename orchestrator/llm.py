import time
from google import genai
from google.genai import types

def call_llm(prompt, model="gemini-3.1-flash-lite", retries=1):
    client = genai.Client(http_options=types.HttpOptions(timeout=30000))
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text
        except Exception as error:
            last_error = error
            print("LLM call failed (attempt " + str(attempt + 1) + "):", error)
            time.sleep(2)
    raise last_error

if __name__ == "__main__":
    print(call_llm("Say hello in three words."))
