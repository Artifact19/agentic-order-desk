import json
import re
import threading


class LocalLLM:
    def __init__(self, model_id: str, device: str):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self.model_id = model_id
        self.device = device
        from .config import model_source

        source = model_source(model_id)
        self.tokenizer = AutoTokenizer.from_pretrained(source)
        self.model = AutoModelForCausalLM.from_pretrained(
            source, dtype=torch.float16 if device == "cuda" else torch.float32).to(device)
        self.model.eval()
        self._lock = threading.Lock()

    def generate(self, system_prompt: str, user_prompt: str, history=None, max_new_tokens: int = 160) -> str:
        messages = [{"role": "system", "content": system_prompt}, *(history or []),
                    {"role": "user", "content": user_prompt}]
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        with self._lock, self._torch.no_grad():
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
            output_ids = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                                             temperature=None, top_p=None, top_k=None,
                                             pad_token_id=self.tokenizer.eos_token_id)
        new_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


class DisabledLLM:
    model_id = "disabled"
    device = "none"

    def generate(self, system_prompt: str, user_prompt: str, history=None, max_new_tokens: int = 160) -> str:
        return ""


def load_llm(model_id: str, enabled: bool):
    if not enabled:
        return DisabledLLM(), "disabled by configuration"
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        llm = LocalLLM(model_id, device)
        llm.generate("Reply with OK.", "ping", max_new_tokens=4)
        return llm, f"loaded on {device}"
    except Exception as error:
        return DisabledLLM(), f"unavailable ({type(error).__name__}); rule-based fallback active"


def extract_json(text: str):
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    for candidate in re.findall(r"\{[^{}]*\}", cleaned, flags=re.S):
        for attempt in (candidate, candidate.replace("'", '"').replace("True", "true")
                        .replace("False", "false").replace("None", "null")):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None
