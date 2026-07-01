import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

def query_openrouter(api_key, model, prompt, system_prompt="You are a professional Python coder."):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000", # Optional, for OpenRouter analytics
        "X-Title": "LeetCode Auto Solver"
    }
    
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        res_json = response.json()
        if "choices" in res_json and len(res_json["choices"]) > 0:
            return res_json["choices"][0]["message"]["content"]
        else:
            return f"Error: Unexpected response format from OpenRouter: {json.dumps(res_json)}"
    except Exception as e:
        return f"Error querying OpenRouter: {str(e)}"

def query_gemini(api_key, model, prompt, system_prompt="You are a professional Python coder."):
    # Google AI Studio API for Gemini
    # standard endpoint for Gemini 2.5 Flash is:
    # https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=...
    # We will use model name dynamically
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {
        "Content-Type": "application/json"
    }
    
    # We combine system prompt and user prompt for Gemini if systemInstruction is not used, 
    # but the API supports systemInstruction. Let's write a robust payload.
    data = {
        "contents": [
            {
                "parts": [
                    {"text": f"{system_prompt}\n\nTask: {prompt}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        res_json = response.json()
        if "candidates" in res_json and len(res_json["candidates"]) > 0:
            candidate = res_json["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"] and len(candidate["content"]["parts"]) > 0:
                return candidate["content"]["parts"][0]["text"]
        return f"Error: Unexpected response format from Gemini: {json.dumps(res_json)}"
    except Exception as e:
        return f"Error querying Gemini: {str(e)}"

def get_code_solution(provider, api_key, model, problem_desc, template_code, language="Python3"):
    if language.lower() in ["mysql", "postgresql", "oracle", "ms sql server", "sql"]:
        system_prompt = (
            f"You are an expert database administrator and SQL developer. Solve the LeetCode problem using {language}. "
            f"Provide ONLY the executable {language} query statement. "
            f"Do NOT include markdown syntax (like ```sql ... ``` or ```{language} ... ```), explanations, "
            f"or comments. The response must contain only the SQL query code."
        )
        prompt = (
            f"Problem Description:\n{problem_desc}\n\n"
            f"Initial Template Code:\n{template_code}\n\n"
            f"Please write the complete SQL query solving this problem in {language}. Follow the template exactly."
        )
    else:
        system_prompt = (
            f"You are an expert software engineer. Solve the LeetCode problem using {language}. "
            f"Provide ONLY the executable {language} code. "
            f"Do NOT include markdown syntax (like ```{language} ... ```), explanations, docstrings, "
            f"comments or testing scripts. The response must contain only the code, starting "
            f"with the class, function, or method definition matching the initial template code."
        )
        prompt = (
            f"Problem Description:\n{problem_desc}\n\n"
            f"Initial Template Code:\n{template_code}\n\n"
            f"Please write the complete code solving this problem in {language}. Follow the template EXACTLY."
        )
    
    if provider.lower() == "openrouter":
        code = query_openrouter(api_key, model, prompt, system_prompt)
    else:
        # Default to Gemini
        code = query_gemini(api_key, model, prompt, system_prompt)
        
    return clean_code(code, language)

def fix_code_solution(provider, api_key, model, problem_desc, failed_code, error_message, language="Python3"):
    if language.lower() in ["mysql", "postgresql", "oracle", "ms sql server", "sql"]:
        system_prompt = (
            f"You are an expert database administrator and SQL developer. Fix the provided {language} query which failed "
            f"on LeetCode. Provide ONLY the corrected executable {language} query. "
            f"Do NOT include markdown syntax (like ```sql ... ``` or ```{language} ... ```), explanations, or comments. "
            f"The response must contain only the SQL query code."
        )
        prompt = (
            f"Problem Description:\n{problem_desc}\n\n"
            f"SQL Query that failed:\n{failed_code}\n\n"
            f"Error message / Failure result:\n{error_message}\n\n"
            f"Please correct the query to solve the issue. Ensure it is correct syntax and runs successfully."
        )
    else:
        system_prompt = (
            f"You are an expert software engineer. Fix the provided {language} code which failed "
            f"on LeetCode. Provide ONLY the corrected executable {language} code. "
            f"Do NOT include markdown syntax (like ```{language} ... ```), explanations, "
            f"docstrings, or comments. The response must contain only the code, starting with the class "
            f"or function definition matching the template."
        )
        prompt = (
            f"Problem Description:\n{problem_desc}\n\n"
            f"Code that failed:\n{failed_code}\n\n"
            f"Error message / Failure result:\n{error_message}\n\n"
            f"Please correct the code to solve the issue. Ensure it compiles and runs correctly."
        )
    
    if provider.lower() == "openrouter":
        code = query_openrouter(api_key, model, prompt, system_prompt)
    else:
        code = query_gemini(api_key, model, prompt, system_prompt)
        
    return clean_code(code, language)

def clean_code(code_text, language="Python3"):
    """
    Cleans up the AI response to make sure we only have raw code without markdown code blocks.
    """
    if not code_text:
        return ""
        
    # Remove markdown code blocks if present
    lines = code_text.strip().split("\n")
    cleaned_lines = []
    
    in_code_block = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            cleaned_lines.append(line)
            
    code = "\n".join(cleaned_lines).strip()
    
    # Strip any extra text that might be outside of a code block (some models return text anyway)
    # Only strip prefix for languages that commonly use class Solution (Python, Java, C#, C++)
    lower_lang = language.lower()
    if "python" in lower_lang or "java" in lower_lang or "c++" in lower_lang or "c#" in lower_lang:
        idx = code.find("class Solution")
        if idx != -1:
            code = code[idx:]
            
    return code
