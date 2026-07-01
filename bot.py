import os
import asyncio
import shutil
from playwright.async_api import async_playwright
from ai import get_code_solution, fix_code_solution

# Topic URL mappings
TOPIC_URLS = {
    "all": "https://leetcode.com/problemset/",
    "algorithms": "https://leetcode.com/problemset/algorithms/",
    "database": "https://leetcode.com/problemset/database/",
    "shell": "https://leetcode.com/problemset/shell/",
    "concurrency": "https://leetcode.com/problemset/concurrency/",
    "javascript": "https://leetcode.com/problemset/javascript/",
    "pandas": "https://leetcode.com/problemset/pandas/"
}

async def type_in_editor(page, code, typing_delay_ms):
    """
    Simulates typing code inside LeetCode's Monaco Editor character-by-character.
    Uses Monaco's API to update the text to look like typing with a block cursor.
    This prevents auto-closing brackets and auto-indentation from corrupting the code.
    """
    js_script = """
    (args) => {
        const code = args.code;
        const delay = args.delay;
        return new Promise((resolve) => {
            const editors = monaco.editor.getEditors();
            const editor = editors.find(e => e.getDomNode() && e.getDomNode().offsetParent !== null) || editors[0];
            if (!editor) {
                resolve(false);
                return;
            }
            const model = editor.getModel();
            if (!model) {
                resolve(false);
                return;
            }
            let index = 0;
            
            function type() {
                if (index <= code.length) {
                    const current = code.substring(0, index);
                    // Add block cursor character for realistic visual typing
                    const cursor = (index < code.length && index % 2 === 0) ? "█" : "";
                    model.setValue(current + cursor);
                    index++;
                    setTimeout(type, delay);
                } else {
                    model.setValue(code); // Final set without cursor
                    resolve(true);
                }
            }
            type();
        });
    }
    """
    await page.evaluate(js_script, {"code": code, "delay": typing_delay_ms})

async def wait_for_submission_result(page, logger):
    """
    Polls the LeetCode page to wait for the submission or run task to finish.
    Returns status and error/output details.
    """
    logger("[Submit] Waiting for evaluation results...")
    for i in range(45):
        result = await page.evaluate("""() => {
            const text = document.body.innerText;
            if (text.includes("Pending") || text.includes("Judging") || text.includes("Running")) {
                return { completed: false };
            }
            
            // Check for Accepted
            const acceptedElements = Array.from(document.querySelectorAll('.text-green-500, .text-emerald-500, .text-green-s, [class*="Accepted"]'));
            let isAccepted = false;
            for (const el of acceptedElements) {
                if (el.innerText.includes("Accepted")) {
                    isAccepted = true;
                    break;
                }
            }
            
            if (isAccepted || text.includes("Accepted")) {
                return { completed: true, status: "Accepted" };
            }
            
            // Check for Failures
            const failures = [
                "Wrong Answer", 
                "Compile Error", 
                "Runtime Error", 
                "Time Limit Exceeded", 
                "Memory Limit Exceeded",
                "Output Limit Exceeded"
            ];
            
            for (const fail of failures) {
                if (text.includes(fail)) {
                    let details = "";
                    
                    // Look for error tracebacks or compiler messages
                    const pre = document.querySelector('pre, code, [class*="err"], [class*="traceback"]');
                    if (pre) {
                        details += pre.innerText + "\\n";
                    }
                    
                    // Look for wrong answer test cases
                    if (fail === "Wrong Answer") {
                        const divs = Array.from(document.querySelectorAll('div'));
                        let wrong_details = [];
                        for (const div of divs) {
                            if (div.innerText && (div.innerText.includes("Input") || div.innerText.includes("Output") || div.innerText.includes("Expected"))) {
                                if (div.innerText.length < 300) {
                                    wrong_details.push(div.innerText);
                                }
                            }
                        }
                        if (wrong_details.length > 0) {
                            details += wrong_details.slice(0, 5).join("\\n");
                        }
                    }
                    
                    return { completed: true, status: fail, details: details.trim() };
                }
            }
            
            return { completed: false };
        }""")
        
        if result and result.get("completed"):
            return result
        await asyncio.sleep(1)
        
    return {"completed": True, "status": "Timeout", "details": "Submission evaluation timed out."}

async def auto_select_editor_language(page, logger, topic=None):
    """
    Scans the editor to find the active programming language dropdown, opens it,
    and selects the highest preferred language that is available in the LeetCode dropdown options,
    expressly avoiding C, C++, and Ruby (unless in a SQL database context).
    """
    logger("[Editor] Checking available programming languages...")
    
    # Use robust JS matching to find the active language button and mark it
    js_detect = """
    () => {
        const langs = ["Python3", "Python", "Java", "C++", "C", "C#", "JavaScript", "TypeScript", "Go", "Rust", "Ruby", "Swift", "Kotlin", "Scala", "PHP", "MySQL", "PostgreSQL", "Bash", "Elixir", "Erlang", "Oracle", "MS SQL Server"];
        const buttons = Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null);
        for (const btn of buttons) {
            const text = btn.innerText.trim();
            if (!text) continue;
            
            // Remove common dropdown chevron/arrow characters and trim
            const cleanedText = text.replace(/[▼▾▶◀▸◂▼▽▾▿▾]/g, '').trim();
            if (langs.includes(cleanedText)) {
                btn.setAttribute('data-active-lang-btn', 'true');
                return cleanedText;
            }
        }
        return null;
    }
    """
    
    active_lang = await page.evaluate(js_detect)
    lang_btn = None
    if active_lang:
        lang_btn = page.locator('[data-active-lang-btn="true"]').first
        
    if not lang_btn or not active_lang:
        logger("[Editor] Warning: Language selection button not found. Assuming Python3.")
        return "Python3"
        
    logger(f"[Editor] Current editor language detected: {active_lang}")
    
    # Preference order for programming languages (excluding C++, C, Ruby)
    preferred_order = ["Python3", "Python", "Java", "JavaScript", "TypeScript", "PHP", "Go", "Rust", "MySQL", "PostgreSQL", "Bash"]
    
    if topic:
        topic_lower = topic.lower()
        if "javascript" in topic_lower:
            preferred_order = ["JavaScript"]
        elif "algorithm" in topic_lower:
            preferred_order = ["Java"]
        elif "database" in topic_lower:
            preferred_order = ["MySQL", "PostgreSQL", "MS SQL Server", "Oracle"]

    # Check if the active language is already preferred
    is_preferred = False
    if topic and "database" in topic.lower():
        is_preferred = active_lang in ["MySQL", "PostgreSQL", "MS SQL Server", "Oracle"]
    else:
        is_preferred = active_lang == preferred_order[0] or (active_lang in preferred_order and active_lang in ["Python3", "Python", "MySQL", "Bash"])

    if is_preferred:
        logger(f"[Editor] Language '{active_lang}' is already preferred. No change needed.")
        try:
            await page.evaluate("() => { const b = document.querySelector('[data-active-lang-btn]'); if (b) b.removeAttribute('data-active-lang-btn'); }")
        except:
            pass
        return active_lang
        
    try:
        # Click dropdown to open option menu
        await lang_btn.click()
        await page.wait_for_timeout(1500)
        
        # Scan and click the first matching preferred option
        for target in preferred_order:
            option_selectors = [
                f'div[role="option"]:has-text("{target}")',
                f'li:has-text("{target}")',
                f'div >> text="{target}"',
            ]
            clicked = False
            for sel in option_selectors:
                try:
                    opt = page.locator(sel).first
                    if await opt.is_visible(timeout=500):
                        await opt.click()
                        logger(f"[Editor] Switched language from {active_lang} to {target}.")
                        await page.wait_for_timeout(1000)
                        clicked = True
                        break
                except:
                    continue
            
            if clicked:
                try:
                    await page.evaluate("() => { const b = document.querySelector('[data-active-lang-btn]'); if (b) b.removeAttribute('data-active-lang-btn'); }")
                except:
                    pass
                return target
            
            # Also try using JavaScript to find and click the option by exact text
            try:
                found = await page.evaluate(f"""
                    () => {{
                        const items = document.querySelectorAll('div, li, span');
                        for (const el of items) {{
                            if (el.textContent.trim() === '{target}' && el.offsetParent !== null) {{
                                el.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if found:
                    logger(f"[Editor] Switched language from {active_lang} to {target} (via JS click).")
                    await page.wait_for_timeout(1000)
                    try:
                        await page.evaluate("() => { const b = document.querySelector('[data-active-lang-btn]'); if (b) b.removeAttribute('data-active-lang-btn'); }")
                    except:
                        pass
                    return target
            except:
                continue
                
        # If no preferred option is found, press Escape to close dropdown
        await page.keyboard.press("Escape")
        logger(f"[Editor] No preferred alternative language found. Proceeding with: {active_lang}")
    except Exception as e:
        logger(f"[Editor Warning] Error switching language: {str(e)}")
        try:
            await page.keyboard.press("Escape")
        except:
            pass
        
    try:
        await page.evaluate("() => { const b = document.querySelector('[data-active-lang-btn]'); if (b) b.removeAttribute('data-active-lang-btn'); }")
    except:
        pass
        
    return active_lang

async def run_leetcode_solver(config, logger, state_ref):
    """
    Main automation loop for LeetCode.
    """
    email = config.get("email")
    password = config.get("password")
    provider = config.get("provider", "gemini")
    model = config.get("model")
    api_key = config.get("api_key")
    difficulty = config.get("difficulty", "EASY").upper()
    topic = config.get("topic", "algorithms")
    typing_delay = int(config.get("typing_delay", 10))
    
    logger(f"[System] Starting Bot with topic={topic}, difficulty={difficulty}, provider={provider}, model={model}")
    
    # Persistent profile folder to store cookies and avoid recaptchas
    profile_dir = os.path.join(os.getcwd(), "leetcode_profile")
    logger("[System] Loading persistent session cookies to bypass human verification on future runs...")
    
    async with async_playwright() as p:
        logger("[Browser] Launching browser window in Incognito mode...")
        # Try launching standard Chrome first, fallback to default chromium if not found
        try:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                channel="chrome",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--incognito"
                ],
                ignore_default_args=["--enable-automation"],
                no_viewport=True
            )
        except Exception:
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--incognito"
                ],
                ignore_default_args=["--enable-automation"],
                no_viewport=True
            )
            
        # Mask webdriver flag to prevent Cloudflare from blocking reCAPTCHA/Turnstile widget load
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.pages[0]
        
        # Set default timeouts to 90 seconds to handle slow network loads
        page.set_default_navigation_timeout(90000)
        page.set_default_timeout(90000)
        
        # 1. Login or check session
        logger("[Auth] Checking if already logged in...")
        logged_in = False
        try:
            # Load with a commit wait status to prevent timeouts on slow third party trackers
            await page.goto("https://leetcode.com/accounts/login/", wait_until="commit")
            await page.wait_for_timeout(5000)
            
            # If redirected, we are already logged in
            if "login" not in page.url and "accounts" not in page.url:
                logger("[Auth] Session active. Already logged in!")
                logged_in = True
            else:
                # Fill email
                email_selectors = ['input[name="login"]', 'input#id_login', 'input[placeholder*="Username"]', 'input[placeholder*="email"]']
                email_filled = False
                for sel in email_selectors:
                    try:
                        if await page.locator(sel).is_visible():
                            await page.fill(sel, email)
                            email_filled = True
                            break
                    except:
                        continue
                        
                # Fill password
                pwd_selectors = ['input[name="password"]', 'input#id_password', 'input[placeholder*="Password"]']
                pwd_filled = False
                for sel in pwd_selectors:
                    try:
                        if await page.locator(sel).is_visible():
                            await page.fill(sel, password)
                            pwd_filled = True
                            break
                    except:
                        continue
                        
                # Submit login form
                if email_filled and pwd_filled:
                    logger("[Auth] Submitting login credentials...")
                    btn_selectors = ['button#signin_btn', 'button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Login")']
                    for sel in btn_selectors:
                        try:
                            btn = page.locator(sel).first
                            if await btn.is_visible():
                                await btn.click()
                                break
                        except:
                            continue
                            
                logger("[Auth] Waiting for login completion. If CAPTCHA or OTP occurs, please complete it manually in the browser window.")
                
                # Wait for user to successfully login (URL does not contain login/accounts)
                for _ in range(60): # 5 minutes timeout
                    if state_ref.get("stop_requested"):
                        logger("[System] Stop requested by user.")
                        await context.close()
                        return
                    current_url = page.url
                    if "login" not in current_url and "accounts" not in current_url:
                        logged_in = True
                        break
                    await asyncio.sleep(5)
                    
            if not logged_in:
                logger("[Auth] Login check timed out. Trying to proceed anyway...")
            else:
                logger("[Auth] Login detected successfully!")
                
            if not state_ref.get("stop_requested"):
                await page.wait_for_timeout(3000)
        except Exception as e:
            if "closed" in str(e).lower() or "target" in str(e).lower():
                logger("[Auth] Browser closed during login process.")
                return
            logger(f"[Auth Error] Unexpected issue during authentication: {str(e)}")
        
        # Topic Loop
        try:
            retry_nav_count = 0
            attempted_slugs = set()  # Track problems we've already solved or skipped
            page_num = 1
            
            while not state_ref.get("stop_requested"):
                try:
                    topic_url = TOPIC_URLS.get(topic, TOPIC_URLS["algorithms"])
                    # Format url parameters: Status = Todo (unsolved), Difficulty = Easy/Medium/Hard
                    target_url = f"{topic_url}?difficulty={difficulty}&status=TODO&page={page_num}"
                    
                    logger(f"[Navigation] Navigating to topic list (page {page_num}): {target_url}")
                    try:
                        await page.goto(target_url, wait_until="commit")
                        await asyncio.sleep(6)
                        retry_nav_count = 0 # Reset on success
                    except Exception as nav_err:
                        if "closed" in str(nav_err).lower() or "target" in str(nav_err).lower():
                            raise nav_err
                        retry_nav_count += 1
                        if retry_nav_count >= 5:
                            logger(f"[Navigation Error] Failed to load topic list 5 times: {str(nav_err)}. Exiting solver.")
                            break
                        logger(f"[Navigation Error] Failed to load topic list (Attempt {retry_nav_count}/5). Retrying in 15s... Error: {str(nav_err)}")
                        await asyncio.sleep(15)
                        continue
                    
                    if state_ref.get("stop_requested"):
                        break
                        
                    # Wait for problem list to render
                    try:
                        await page.wait_for_selector('a[href*="/problems/"]', timeout=15000)
                    except:
                        logger("[Navigation] Problem list did not load. Retrying...")
                        retry_nav_count += 1
                        continue
                    
                    # Find all problem links on this page
                    problem_hrefs = await page.evaluate("""() => {
                        const links = Array.from(document.querySelectorAll('a[href*="/problems/"]'));
                        return links
                            .map(a => a.getAttribute('href').split('?')[0])
                            .filter(href => {
                                const parts = href.split('/').filter(Boolean);
                                return parts.length === 2 && parts[0] === 'problems';
                            });
                    }""")
                    
                    # Clean duplicates
                    unique_hrefs = []
                    for href in problem_hrefs:
                        if href not in unique_hrefs:
                            unique_hrefs.append(href)
                    
                    # Extract slug from href (e.g. "/problems/two-sum/" -> "two-sum")
                    def get_slug(href):
                        return href.strip("/").split("/")[-1].split("?")[0]
                            
                    if not unique_hrefs:
                        logger("[System] No unsolved tasks found matching the criteria. We are all done!")
                        break
                    
                    # Filter out already attempted problems
                    remaining = [h for h in unique_hrefs if get_slug(h) not in attempted_slugs]
                    
                    if not remaining:
                        # All problems on this page were already attempted, go to next page
                        page_num += 1
                        logger(f"[Navigation] All problems on current page already attempted. Moving to page {page_num}...")
                        continue
                        
                    chosen_href = remaining[0]
                    chosen_slug = get_slug(chosen_href)
                    next_problem_url = f"https://leetcode.com{chosen_href}"
                    logger(f"[Problem] Next unsolved task found: {chosen_slug} -> Opening {next_problem_url}")
                    
                    # Navigate to problem
                    try:
                        await page.goto(next_problem_url, wait_until="commit")
                        await asyncio.sleep(5)
                    except Exception as nav_err:
                        if "closed" in str(nav_err).lower() or "target" in str(nav_err).lower():
                            raise nav_err
                        logger(f"[Navigation Error] Failed to open problem page. Retrying list navigation... Error: {str(nav_err)}")
                        await asyncio.sleep(5)
                        continue
                    
                    # Wait for description and editor to load
                    logger("[Workspace] Waiting for editor and problem description to load...")
                    try:
                        await page.wait_for_selector('.monaco-editor', timeout=30000)
                        # Wait for description panel
                        desc_sel = '[data-track-load="description_content"]'
                        await page.wait_for_selector(desc_sel, timeout=30000)
                    except Exception as e:
                        logger("[Workspace] Timeout waiting for workspace components. Retrying page load...")
                        attempted_slugs.add(chosen_slug)
                        continue
                        
                    await page.wait_for_timeout(3000)
                    
                    if state_ref.get("stop_requested"):
                        break
                    
                    # Check if this problem is already solved ("Solved" badge on problem page)
                    is_already_solved = await page.evaluate("""() => {
                        const text = document.body.innerText;
                        // Check for "Solved" badge near the problem title
                        const solvedBadges = document.querySelectorAll('[class*="text-green"], [class*="success"]');
                        for (const el of solvedBadges) {
                            if (el.textContent.trim() === 'Solved') return true;
                        }
                        return false;
                    }""")
                    
                    if is_already_solved:
                        logger(f"[Navigation] Problem '{chosen_slug}' is already solved. Skipping to next...")
                        attempted_slugs.add(chosen_slug)
                        await asyncio.sleep(1)
                        continue
                        
                    # Extract description text
                    desc_text = await page.locator('[data-track-load="description_content"]').inner_text()
                    logger(f"[Workspace] Description read successful ({len(desc_text)} chars).")
                    
                    # Switch language automatically based on supported languages (excluding C++, C, Ruby, etc.)
                    selected_lang = await auto_select_editor_language(page, logger, topic=topic)
                    
                    # Extract Monaco Editor template
                    template_code = await page.evaluate("""() => {
                        const editors = monaco.editor.getEditors();
                        const editor = editors.find(e => e.getDomNode() && e.getDomNode().offsetParent !== null) || editors[0];
                        return editor ? editor.getValue() : '';
                    }""")
                    logger(f"[Workspace] Extracted initial template code.")
                    
                    # 2. AI solution generation
                    logger(f"[AI] Generating code solution in {selected_lang} via {provider} ({model})...")
                    ai_code = get_code_solution(provider, api_key, model, desc_text, template_code, selected_lang)
                    
                    if not ai_code or "Error" in ai_code:
                        logger(f"[Error] AI failed to generate solution: {ai_code}. Skipping problem...")
                        attempted_slugs.add(chosen_slug)
                        await asyncio.sleep(5)
                        continue
                        
                    logger("[Workspace] Typing AI-generated code solution at human speed...")
                    # Type code
                    await type_in_editor(page, ai_code, typing_delay)
                    logger("[Workspace] Code typing complete.")
                    await page.wait_for_timeout(2000)
                    
                    # 3. Submit loop
                    retry_count = 0
                    max_retries = 5
                    solved = False
                    
                    while retry_count < max_retries and not solved and not state_ref.get("stop_requested"):
                        logger("[Submit] Clicking Submit button...")
                        
                        # Click submit button
                        submit_clicked = False
                        submit_selectors = [
                            'button[data-cy="submit-code-btn"]',
                            'button:has-text("Submit")',
                            'button[class*="submit"]',
                            'div[class*="submit"] button'
                        ]
                        
                        for sel in submit_selectors:
                            try:
                                btn = page.locator(sel).first
                                if await btn.is_visible():
                                    await btn.click()
                                    submit_clicked = True
                                    break
                            except:
                                continue
                                
                        if not submit_clicked:
                            logger("[Error] Could not click Submit button. Retrying submission...")
                            retry_count += 1
                            await asyncio.sleep(5)
                            continue
                            
                        # Wait for submission result
                        result = await wait_for_submission_result(page, logger)
                        status = result.get("status")
                        details = result.get("details", "")
                        
                        logger(f"[Submit] Submission Result: {status}")
                        
                        if status == "Accepted":
                            logger("[Success] Task solved successfully! 🎉")
                            solved = True
                            # Let it show success in the browser for a bit
                            await page.wait_for_timeout(5000)
                        else:
                            logger(f"[Failure] Submission failed: {status}. Error details: {details[:200]}...")
                            retry_count += 1
                            if retry_count >= max_retries:
                                logger("[System] Max retries reached for this problem. Moving on.")
                                break
                                
                            logger(f"[AI] Requesting code correction (Retry {retry_count}/{max_retries})...")
                            current_code = await page.evaluate("""() => {
                                const editors = monaco.editor.getEditors();
                                const editor = editors.find(e => e.getDomNode() && e.getDomNode().offsetParent !== null) || editors[0];
                                return editor ? editor.getValue() : '';
                            }""")
                            ai_code = fix_code_solution(provider, api_key, model, desc_text, current_code, f"Status: {status}\nDetails: {details}", selected_lang)
                            
                            if not ai_code or "Error" in ai_code:
                                logger(f"[Error] AI failed to fix code. Retrying in 10s...")
                                await asyncio.sleep(10)
                                continue
                                
                            logger("[Workspace] Re-typing corrected code solution...")
                            await type_in_editor(page, ai_code, typing_delay)
                            await page.wait_for_timeout(2000)
                    
                    # Mark this problem as attempted so we never re-pick it
                    attempted_slugs.add(chosen_slug)
                    
                    if solved:
                        logger(f"[Navigation] Problem '{chosen_slug}' solved! Moving to next problem...")
                    else:
                        logger(f"[Navigation] Problem '{chosen_slug}' could not be solved. Skipping to next problem...")
                    await asyncio.sleep(3)
                except Exception as loop_err:
                    if "closed" in str(loop_err).lower() or "target" in str(loop_err).lower():
                        raise loop_err
                    logger(f"[System Error] Unexpected error during solver loop: {str(loop_err)}. Retrying next problem...")
                    await asyncio.sleep(10)
        except Exception as e:
            if "closed" in str(e).lower() or "target" in str(e).lower():
                logger("[System] Browser was closed or target page disappeared. Exiting automator.")
            else:
                logger(f"[System Error] Critical error during solver thread: {str(e)}")
            
        logger("[System] Closing browser context.")
        try:
            await context.close()
        except:
            pass
        logger("[System] Bot completed run.")
