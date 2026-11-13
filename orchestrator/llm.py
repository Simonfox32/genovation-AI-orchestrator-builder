import time
from google import genai
from google.genai import types

def call_llm(prompt, model="gemini-3.1-flash-lite", retries=1, json_mode=False):
    """Ask Gemini and return its text reply.

    json_mode=True makes Gemini return only valid JSON (no Python tuples, no stray text),
    which the decomposer needs: without it, about half of the real plans failed to parse.
    """
    client = genai.Client(http_options=types.HttpOptions(timeout=30000))
    config = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            return response.text
        except Exception as error:
            last_error = error
            print("LLM call failed (attempt " + str(attempt + 1) + "):", error)
            time.sleep(2)
    raise last_error

if __name__ == "__main__":
    print(call_llm("Say hello in three words."))
