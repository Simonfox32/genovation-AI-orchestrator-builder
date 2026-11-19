import time
from google import genai
from google.genai import types

BACKUP_MODEL = "gemini-3.5-flash-lite"

def call_llm(prompt, model="gemini-3.1-flash-lite", retries=3):
    client = genai.Client(http_options=types.HttpOptions(timeout=20000))
    last_error = None
    for attempt in range(retries + 1):
        # Every other attempt uses the backup model, in case one model is overloaded.
        use_model = model if attempt % 2 == 0 else BACKUP_MODEL
        try:
            response = client.models.generate_content(model=use_model, contents=prompt)
            return response.text
        except Exception as error:
            last_error = error
            print("LLM call failed (attempt " + str(attempt + 1) + ", " + use_model + "):", error)
            if "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error):
                time.sleep(10)  # rate limit: wait longer
            else:
                time.sleep(2 * (attempt + 1))
    raise last_error

if __name__ == "__main__":
    print(call_llm("Say hello in three words."))
