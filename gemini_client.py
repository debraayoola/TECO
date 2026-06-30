"""
utils/gemini_client.py
Async wrapper around the Google Gemini API (generativelanguage.googleapis.com),
using a key from Google AI Studio. Handles chat, summarization, and
prompt-based moderation/toxicity judgment.
"""

import aiohttp
import os
import json
import logging

logger = logging.getLogger('CoAdminBot.Gemini')

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
# 'gemini-flash-latest' is a rolling alias Google hot-swaps to the newest
# stable Flash model — avoids hardcoding a version that gets deprecated.
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-flash-latest')
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta/models'

SYSTEM_PROMPT = (
    "You are an AI co-admin assistant for a Discord server. "
    "You are helpful, concise, friendly, and enforce community guidelines "
    "when asked. Keep responses under 150 words unless asked for detail."
)


class GeminiClient:
    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning('GEMINI_API_KEY is not set — AI commands will fail.')
        self.headers = {'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_API_KEY}

    async def _generate(self, contents: list, system_instruction: str = None,
                        max_tokens: int = 400, temperature: float = 0.7) -> str:
        url = f'{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent'
        payload = {
            'contents': contents,
            'generationConfig': {
                'maxOutputTokens': max_tokens,
                'temperature': temperature,
            }
        }
        if system_instruction:
            payload['systemInstruction'] = {'parts': [{'text': system_instruction}]}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=payload, timeout=30) as resp:
                data = await resp.json()
                if resp.status != 200:
                    err = data.get('error', {}).get('message', str(data)) if isinstance(data, dict) else str(data)
                    logger.error(f'Gemini API error ({resp.status}): {err}')
                    raise RuntimeError(err)

                try:
                    candidate = data['candidates'][0]
                    parts = candidate['content']['parts']
                    return ''.join(p.get('text', '') for p in parts).strip()
                except (KeyError, IndexError):
                    finish_reason = data.get('candidates', [{}])[0].get('finishReason', 'UNKNOWN')
                    logger.error(f'Unexpected Gemini response shape, finishReason={finish_reason}: {data}')
                    if finish_reason == 'SAFETY':
                        return '⚠️ My safety filters blocked a response to that prompt.'
                    return "I couldn't generate a response for that."

    # ─── Chat ─────────────────────────────────────────────────────────────────

    async def chat(self, prompt: str, context: list = None, max_tokens: int = 300) -> str:
        """
        `context` is a list of {'role': 'user'|'assistant', 'content': str}.
        Gemini uses role 'model' instead of 'assistant'.
        """
        contents = []
        if context:
            for turn in context[-6:]:
                role = 'user' if turn['role'] == 'user' else 'model'
                contents.append({'role': role, 'parts': [{'text': turn['content']}]})
        contents.append({'role': 'user', 'parts': [{'text': prompt}]})

        try:
            return await self._generate(contents, system_instruction=SYSTEM_PROMPT, max_tokens=max_tokens)
        except RuntimeError as e:
            return f'⚠️ AI error: {e}'

    # ─── Summarization ────────────────────────────────────────────────────────

    async def summarize(self, text: str, max_words: int = 120) -> str:
        if len(text.split()) < 30:
            return text  # too short to bother summarizing

        prompt = (
            f'Summarize the following Discord chat log in no more than {max_words} words. '
            f'Focus on topics discussed and any decisions/action items. '
            f'Do not include usernames unless essential.\n\n{text[:6000]}'
        )
        try:
            return await self._generate(
                [{'role': 'user', 'parts': [{'text': prompt}]}],
                max_tokens=300, temperature=0.3
            )
        except RuntimeError as e:
            return f'⚠️ Summarization error: {e}'

    # ─── Toxicity / Moderation Judgment ─────────────────────────────────────────

    async def check_toxicity(self, text: str) -> dict:
        """
        Asks Gemini to classify text and returns
        {'label': str, 'score': float, 'is_toxic': bool, 'reason': str}.
        """
        prompt = (
            'Classify the following message for Discord community-guideline violations '
            '(harassment, hate speech, threats, sexual content, severe profanity). '
            'Respond with ONLY a JSON object, no markdown, no extra text, in this exact shape: '
            '{"label": "clean|toxic", "score": 0.0_to_1.0, "reason": "one short sentence"}\n\n'
            f'Message: "{text}"'
        )
        try:
            raw = await self._generate(
                [{'role': 'user', 'parts': [{'text': prompt}]}],
                max_tokens=100, temperature=0.0
            )
            cleaned = raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            parsed = json.loads(cleaned)
            label = parsed.get('label', 'unknown')
            score = float(parsed.get('score', 0.0))
            return {
                'label': label,
                'score': round(score, 3),
                'is_toxic': label == 'toxic' and score > 0.5,
                'reason': parsed.get('reason', '')
            }
        except (RuntimeError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f'Toxicity check failed to parse: {e}')
            return {'label': 'error', 'score': 0.0, 'is_toxic': False, 'reason': 'Analysis failed'}

    # ─── Moderation Verdict (for /moderate command) ─────────────────────────────

    async def moderation_verdict(self, text: str) -> str:
        prompt = (
            f'A Discord moderator wants to know if this message violates typical '
            f'community guidelines (harassment, hate speech, spam, NSFW, threats). '
            f'Message: "{text}"\n'
            f'Respond with a verdict (SAFE/BORDERLINE/VIOLATION) and a one-sentence reason.'
        )
        try:
            return await self._generate(
                [{'role': 'user', 'parts': [{'text': prompt}]}],
                max_tokens=80, temperature=0.0
            )
        except RuntimeError as e:
            return f'⚠️ AI error: {e}'
