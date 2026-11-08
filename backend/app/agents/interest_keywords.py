"""Keyword fallback for the InterestExtractor when the LLM is unavailable.

Labels match the persona interests in scripts/reference_data.py (PERSONAS), plus a few undergraduate-major
labels. Each keyword matches at a word start (case-insensitive), so "api" matches "APIs" but not "capital".
"""
import re

INTEREST_KEYWORDS: dict[str, list[str]] = {
    # ai_governance_finance
    "AI governance": ["governance", "regulat", "compliance", "responsible ai"],
    "Financial services": ["bank", "financ", "credit", "fintech", "insurance"],
    "Model risk": ["model risk", "model validation", "credit risk", "risk model"],
    # healthcare_analytics
    "Healthcare analytics": ["health", "hospital", "clinical", "patient", "medic"],
    "Data science": ["data scien", "statistic", "dashboard", "a/b test", "analytics"],
    "Public health": ["public health", "epidemiolog", "readmission"],
    # cybersecurity
    "Cybersecurity": ["secur", "cyber", "incident response", "siem", "vulnerab", "threat"],
    "AI security": ["attacks on ml", "adversarial", "securing ai"],
    "Privacy": ["privacy", "gdpr"],
    # product_data_science
    "Product management": ["product manag", "product metric", "roadmap", "products"],
    "Experimentation": ["a/b test", "experiment"],
    # public_policy
    "Public policy": ["policy", "public service", "civic"],
    "Government technology": ["government", "digital service"],
    "AI ethics": ["ethic", "fairness", "bias", "responsible"],
    # ai_agents_engineering
    "AI agents": ["agent", "chatbot", "assistant"],
    "Software engineering": ["software", "backend", "api", "python"],
    "LLM applications": ["llm", "language model", "chatbot", "gpt", "nlp"],
    # strategy_consulting
    "Strategy": ["strateg"],
    "Consulting": ["consult"],
    "Change management": ["transformation", "change manag", "workshop", "process redesign"],
    # mlops_data_engineering
    "MLOps": ["mlops", "deploying models", "model deployment", "devops"],
    "Data engineering": ["pipeline", "data engineer", "etl", "streaming", "database"],
    "Cloud": ["cloud", "aws", "azure", "gcp"],
    # undergraduate majors
    "Machine learning": ["machine learning", "deep learning"],
    "Computer science": ["computer science"],
    "Economics": ["economics"],
    "Statistics": ["statistics major", "degree in statistics"],
}

_PATTERNS = {label: [re.compile(rf"(?<!\w){re.escape(k)}", re.IGNORECASE) for k in keywords]
             for label, keywords in INTEREST_KEYWORDS.items()}


def keyword_labels(text: str, skip: set[str] = frozenset(), limit: int = 5) -> list[str]:
    """Labels whose keywords appear in `text`, most keyword hits first; labels in `skip` (lower case) left out."""
    hits = {label: sum(len(p.findall(text)) for p in patterns) for label, patterns in _PATTERNS.items()
            if label.lower() not in skip}
    ranked = sorted((label for label, n in hits.items() if n), key=lambda label: -hits[label])
    return ranked[:limit]
