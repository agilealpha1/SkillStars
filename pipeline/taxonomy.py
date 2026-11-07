"""SkillStars taxonomy: 10 fixed categories (ids are stable; the site and the label cache key off them),
plus the keyword rules used when ANTHROPIC_API_KEY is missing.

Signals are weighted by how deliberate they are: a repo's name and description are written by its owner for
humans (weight 5 per hit), topics are cheap to add and widely padded (weight 1), and the README excerpt mostly
mentions other tools (at most 2 points per category). The highest score wins; ties go to the earlier category.
"""
import re

CATEGORIES = [
    # id,           label,                          one-line definition (also shown to the LLM and on the site)
    ('skills',     'Claude/Agent Skills',           'Claude Code skills, Agent Skills (SKILL.md) and other packaged, reusable capabilities agents load on demand.'),
    ('mcp',        'MCP Servers',                   'Model Context Protocol servers, clients, registries and SDKs.'),
    ('frameworks', 'Agent Frameworks',              'Libraries and platforms for building, orchestrating or running LLM agents and multi-agent systems.'),
    ('coding',     'Coding Agents and IDE Rules',   'AI coding agents and assistants, and rule/config collections for them (.cursorrules, AGENTS.md, CLAUDE.md).'),
    ('prompts',    'Prompt Libraries',              'Collections of prompts, system prompts and prompt-engineering guides.'),
    ('rag-memory', 'RAG and Memory',                'Retrieval-augmented generation, knowledge bases, vector search and long-term agent memory.'),
    ('browser',    'Browser/Computer Use',          'Agents and tools that drive browsers, desktops or phones, and web scraping for LLMs.'),
    ('evals',      'Evals and Observability',       'Evaluation, testing, tracing, monitoring and guardrails for LLM apps and agents.'),
    ('awesome',    'Awesome Lists',                 'Curated "awesome-*" link lists about agents, skills, MCP, prompts or LLM tools.'),
    ('other',      'Other',                         'In scope, but fits none of the above (e.g. tool/plugin collections, agent apps, datasets).'),
]
CATEGORY_IDS = [c[0] for c in CATEGORIES]
LABELS = {c[0]: c[1] for c in CATEGORIES}
TAXONOMY_VERSION = 1          # bump to relabel everything after changing CATEGORIES

W_HEAD, W_TOPIC, README_CAP = 5, 1, 2

RULES = {
    'skills':     r'\b(agent[- ]skills?|claude[- ]skills?|codex[- ]skills?|skill\.md|skills? (for|library|collection|marketplace|pack)|claude code (skills?|plugins?)|(reusable|packaged) skills?|superpowers)\b',
    'mcp':        r'\b(mcp|model context protocol|mcp[- ]servers?|mcp[- ]clients?|fastmcp)\b',
    'frameworks': r'\b(agents? (framework|sdk|platform|orchestration|runtime|harness)|(build|building|deploy|deploying|create|creating)( and \w+)? (ai[- ]powered |ai |llm )?agents|agents and workflows|agentic (apps|applications)|multi[- ]agents?|autonomous[- ]agents?|langchain|langgraph|llama[- ]?index|autogen|crewai|agentic (framework|workflows?)|workflow (automation|builder)|low[- ]code|build(ing)? (ai )?agents)\b',
    'coding':     r'\b(coding (agents?|assistants?)|in(to)? (your|the) terminal|code (agents?|assistants?)|ai (pair[- ]programm\w*|coding)|cursor[- ]?rules|\.cursorrules|agents\.md|claude\.md|claude[- ]code|copilot|codex|software engineering agents?|swe[- ]?(bench|agent)|terminal (agent|assistant)|vibe[- ]coding|ide)\b',
    'prompts':    r'\b(system[- ]prompts?|prompt[- ]engineering|prompt (library|collection|templates?|optimi[sz]\w*)|(ai|llm|chatgpt|gpt) prompts?|awesome prompts|jailbreaks?|leaked prompts?)\b',
    'rag-memory': r'\b(rag|retrieval[- ]augmented|knowledge (base|graph)s?|vector (database|search|store|db)s?|embeddings?|long[- ]term memory|memory (layer|for (ai|agents|llms))|agent memory|graphrag|document (qa|parsing|chat))\b',
    'browser':    r'\b(browser[- ](use|automation|agents?)|computer[- ]use|web (agents?|automation|scraping|crawl\w*)|crawl4ai|scrap(er|ing)|playwright|puppeteer|selenium|gui agents?|desktop (agent|automation)|phone agent)\b',
    'evals':      r'\b(evals?|evaluations?|llm[- ]evaluation|benchmark\w*|observability|tracing|llmops|guardrails?|red[- ]team\w*|prompt (testing|evaluation))\b',
}
_RX = {k: re.compile(v) for k, v in RULES.items()}

TAG_RULES = [
    ('claude', r'\b(claude|anthropic)\b'), ('openai', r'\b(openai|chatgpt|gpt-?[345o])\b'), ('gemini', r'\bgemini\b'),
    ('mcp', r'\b(mcp|model context protocol)\b'), ('skills', r'\bskills?\b'), ('multi-agent', r'\bmulti[- ]agents?\b'),
    ('rag', r'\b(rag|retrieval[- ]augmented)\b'), ('memory', r'\bmemory\b'), ('browser', r'\bbrowser\b'),
    ('coding', r'\b(coding|code generation|programming)\b'), ('cursor', r'\bcursor\b'), ('prompts', r'\bprompts?\b'),
    ('workflow', r'\bworkflows?\b'), ('low-code', r'\b(low[- ]code|no[- ]code)\b'), ('local-llm', r'\b(local llms?|ollama|self[- ]hosted)\b'),
    ('voice', r'\b(voice|speech|tts)\b'), ('evals', r'\bevals?\b'), ('observability', r'\b(observability|tracing)\b'),
    ('scraping', r'\b(scrap\w+|crawl\w*)\b'), ('knowledge-graph', r'\bknowledge graphs?\b'), ('cli', r'\b(cli|terminal)\b'),
    ('research', r'\b(deep research|research agent)\b'), ('education', r'\b(course|tutorial|for beginners|learn)\b'),
]
_TAG_RX = [(t, re.compile(p)) for t, p in TAG_RULES]

# ---- scope gate (keyword fallback; the LLM decides when a key is set) ----
STRONG = re.compile(
    r'\b(mcp|model context protocol|agentic|claude|anthropic|codex|copilot|cursor ?rules|\.cursorrules|agents\.md|claude\.md|skill\.md|'
    r'(ai|llm|coding|code|autonomous|research|browser|web|computer[- ]use|gui|multi|voice|data) ?[- ]?agents?|'
    r'agents? (framework|sdk|platform|skills?|harness|runtime|orchestration)|(agent|claude|ai|codex|llm) skills?|'
    r'skills? for (ai|agents?|claude|codex|llms?)|langchain|langgraph|llama[- ]?index|autogen|crewai|coding assistants?|'
    r'rag|retrieval[- ]augmented|vector (database|db|store|search)s?|prompt[- ]engineering|(system|ai|llm|chatgpt|gpt) prompts?|'
    r'llm[- ]evaluation|llm evals?|computer[- ]use|browser[- ]use|function[- ]calling|tool[- ]calling|context engineering|'
    r'vibe[- ]coding|agent harness|subagents?|superagent)\b')
WEAK = re.compile(r'\b(agents?|skills?|prompts?|llms?|gpt[\w.-]*|chatgpt|openai|gemini|deepseek|qwen|ai|genai|'
                  r'large language models?|language models?|chatbots?)\b')
AI_WORDS = re.compile(r'\b(ai|llms?|gpt[\w.-]*|chatgpt|openai|gemini|deepseek|qwen|genai|large language models?|language models?|chatbots?)\b')
# Specific topics count only when the name/description shows at least some AI signal: a bare `mcp-server` or
# `agent-skills` topic on a CSS cheatsheet is padding, not evidence.
SPECIFIC_TOPICS = {'claude-skills', 'agent-skills', 'claude-code-skills', 'mcp-server', 'mcp-servers', 'mcp-client',
                   'model-context-protocol', 'cursor-rules', 'cursorrules', 'agent-framework', 'ai-agents-framework',
                   'multi-agent-systems', 'prompt-engineering', 'system-prompts', 'computer-use', 'browser-use', 'agents-md'}
AGENT_TOPICS = {'ai-agents', 'ai-agent', 'agents', 'agent', 'llm-agent', 'autonomous-agents', 'agentic-ai', 'agentic',
                'multi-agent', 'mcp', 'claude-code', 'llm-tools', 'langchain', 'rag', 'prompts', 'chatgpt-prompts', 'llmops'}
NAME_HINT = re.compile(r'(agent|skills|mcp|claude|gpt|llm|codex|copilot|cursor|\brag\b)')   # not 'prompt': shell prompts
NOT_AI_NAME = re.compile(r'(v2ray|zabbix|ssh|user-?agent|skill-?icons|skill-?assessment)')
# Core model infrastructure gives no agent capability by itself.
OUT_OF_SCOPE = re.compile(r'\b(inference (engine|server)|serving (engine|framework)|run and train|train(ing)? (and|&) (run|serve)|'
                          r'fine[- ]tun\w*|pre-?training|quantiz\w+|deep learning framework|tensor library|stable diffusion|'
                          r'image generation|speech (model|synthesis)|text[- ]to[- ]speech|ocr)\b')


def _name(repo):
    return (repo.get('full_name') or '').split('/')[-1].lower()


# Phrases that contain agent words but aren't about AI agents: HTTP user agents, and "for developers and AI agents"
# marketing on general tools (a PHP test framework, a front-end checklist).
NOISE = re.compile(r'\b(user[- ]?agents?|ua[- ]parser|no agents|agentless|'
                   r'for (\w+ ){0,3}(developers|humans|people|teams|engineers) (and|&) (ai )?agents)\b')


def head_text(repo):
    """Name (also with separators as spaces) + description, lowercased, minus NOISE: the owner-written signal."""
    n = _name(repo)
    return NOISE.sub(' ', f"{n} {n.replace('-', ' ').replace('_', ' ')} {repo.get('description') or ''}".lower())


def _weak_terms(text):
    return {m if isinstance(m, str) else m[0] for m in WEAK.findall(text)}


def in_scope(repo):
    head, name = head_text(repo), _name(repo)
    via = repo.get('via') or []
    if 'seed' in via:                                     # hand-curated in seeds.txt
        return True
    topics = set(repo.get('topics') or [])
    ai_signal = bool(STRONG.search(head) or AI_WORDS.search(head))   # 'skills'/'prompts' alone are not AI signal
    if OUT_OF_SCOPE.search(head) and not re.search(r'\b(agents?|agentic|mcp|skills?)\b', head):
        return False
    if NOT_AI_NAME.search(name) and not STRONG.search(head):
        return False
    if STRONG.search(head) or NAME_HINT.search(name) or (ai_signal and SPECIFIC_TOPICS & topics):
        return True
    if via and all(v.startswith('readme:') for v in via):   # README-only matches mostly just mention the tools
        return False
    weak = _weak_terms(head)
    if not weak - {'ai'}:                                    # a lone "AI" (e.g. "...and AI app development") isn't enough
        return False
    return len(weak) + bool(AGENT_TOPICS & topics) >= 2


def is_awesome(repo):
    """Awesome lists are recognized from the name/description only (an `awesome-list` topic is not enough)."""
    head = head_text(repo)
    return _name(repo).startswith('awesome') or bool(re.search(r'\b(curated list|awesome list|awesome-list)\b', head))


def rule_scores(repo):
    head = head_text(repo)
    topics = ' '.join(repo.get('topics') or []).lower().replace('-', ' ')
    body = (repo.get('readme') or '').lower()
    scores = {}
    for cid, rx in _RX.items():
        s = W_HEAD * len(rx.findall(head)) + W_TOPIC * len(rx.findall(topics)) + min(README_CAP, len(rx.findall(body)))
        if s:
            scores[cid] = s
    if is_awesome(repo):
        scores['awesome'] = 1000                       # an awesome list is an awesome list, whatever it lists
    return scores


def rule_classify(repo):
    """Keyword fallback. Returns dict(category, tags, summary, in_scope)."""
    scores = rule_scores(repo)
    cat = max(CATEGORY_IDS[:-1], key=lambda c: (scores.get(c, 0), -CATEGORY_IDS.index(c)))
    if not scores.get(cat):
        cat = 'other'
    text = head_text(repo) + ' ' + ' '.join(repo.get('topics') or []).lower()
    tags = []
    for t, rx in _TAG_RX:
        if rx.search(text) and t not in tags and t != cat:
            tags.append(t)
        if len(tags) == 3:
            break
    desc = re.sub(r'\s+', ' ', repo.get('description') or '').strip()
    summary = desc if len(desc) <= 140 else desc[:137].rsplit(' ', 1)[0] + '…'
    return {'category': cat, 'tags': tags, 'summary': summary, 'in_scope': in_scope(repo)}
