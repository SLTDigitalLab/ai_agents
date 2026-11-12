"""Task-owned filler scheduling. No audio, transcripts, or facts are logged.

The transport must acknowledge playback completion. Missing acknowledgements
fail silent rather than accumulating speech. Each instance belongs to one
authenticated WebSocket and is never shared between users.
"""
import asyncio
import time
from dataclasses import dataclass, field


def normalize_language(value):
    if not isinstance(value, str):
        return None
    code = value.lower().split('-')[0]
    return code if code in {'en', 'si', 'ta'} else None


# Deliberately avoid claims of progress or saved tasks that the executor
# cannot substantiate. Every phrase is unique within its language.
POOLS = {
    'en': (
        ('Let me check that for you.', 'Let me have a look at that.'),
        ('Give me another moment.', 'I am still checking that.'),
        ('Thanks for your patience.', 'I am still working on your request.'),
        ('This is taking a little more time.', 'I am continuing to check.',
         'Thank you for waiting while I check.', 'The request is still processing.',
         'I am waiting for the information.', 'Please bear with me while this runs.'),
    ),
    'si': (
        ('හරි, මම ඒක බලලා කියන්නම්.', 'හරි, මම පොඩ්ඩක් බලන්නම්.'),
        ('තව පොඩ්ඩක් ඉන්න.', 'මම තවම ඒක බලනවා.'),
        ('ඉවසලා හිටියට ස්තුතියි.', 'මම තවම ඔබේ ඉල්ලීම ගැන වැඩ කරනවා.'),
        ('මේකට තව ටිකක් වෙලා යනවා.', 'මම දිගටම ඒක පරීක්ෂා කරනවා.',
         'මම බලන අතරේ රැඳී සිටියාට ස්තුතියි.', 'ඉල්ලීම තවම සැකසෙමින් පවතිනවා.',
         'මම තොරතුරු ලැබෙන තුරු බලාගෙන ඉන්නවා.', 'කරුණාකර තව මොහොතක් රැඳී සිටින්න.'),
    ),
    'ta': (
        ('சரி, நான் அதை பார்த்து சொல்கிறேன்.', 'சரி, நான் அதை பார்த்துக் கொள்கிறேன்.'),
        ('இன்னும் கொஞ்சம் காத்திருக்கவும்.', 'நான் இன்னும் அதை சரிபார்க்கிறேன்.'),
        ('காத்திருந்ததற்கு நன்றி.', 'உங்கள் கோரிக்கையை இன்னும் பரிசீலித்து வருகிறேன்.'),
        ('இதற்கு இன்னும் சிறிது நேரம் தேவைப்படுகிறது.', 'நான் தொடர்ந்து சரிபார்த்து வருகிறேன்.',
         'நான் பார்க்கும் வரை காத்திருப்பதற்கு நன்றி.', 'கோரிக்கை இன்னும் செயலாக்கத்தில் உள்ளது.',
         'தகவல் கிடைக்க காத்திருக்கிறேன்.', 'தயவுசெய்து இன்னும் சிறிது நேரம் பொறுத்திருக்கவும்.'),
    ),
}

STOP_STATES = frozenset({
    'answer_ready', 'completed', 'failed', 'cancelled', 'user_interrupt',
    'waiting_for_user', 'waiting_for_provider', 'cooldown', 'waiting_for_retry',
})


@dataclass
class FillerTask:
    task_id: str
    turn_id: str
    language: str
    started: float
    state: str = 'processing'
    count: int = 0
    used: set = field(default_factory=set)
    pending: str | None = None
    last_end: float = float('-inf')
    runner: asyncio.Task | None = None


class FillerScheduler:
    def __init__(self, send, clock=time.monotonic, lead_time=0):
        self.send = send
        self.clock = clock
        self.lead_time = lead_time
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.active_id = None

    async def start(self, task_id, turn_id, language, *, started=None, run=True):
        language = normalize_language(language)
        if language is None:
            return None  # Unknown is never silently English.
        async with self.lock:
            if task_id in self.tasks:
                return self.tasks[task_id]
            task = FillerTask(task_id, turn_id, language,
                              self.clock() if started is None else started)
            self.tasks[task_id] = task
            self.active_id = task_id
            await self.send({'type': 'filler_task_started', 'task_id': task_id,
                             'turn_id': turn_id, 'language': language})
            if run:
                task.runner = asyncio.create_task(self._run(task))
            return task

    async def _run(self, task):
        try:
            while task.state == 'processing':
                await asyncio.sleep(0.1)
                await self.tick(task.task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            task.state = 'failed'

    async def tick(self, task_id):
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.state != 'processing' or task.pending:
                return
            if self.active_id != task_id:
                return
            deadline = (2.5, 10, 16)[task.count] if task.count < 3 else 24 + 9 * (task.count - 3)
            if self.clock() - task.started < deadline - self.lead_time or self.clock() - task.last_end < 3:
                return
            stage = min(task.count, 3)
            pool = POOLS[task.language][stage]
            phrase = next((p for p in pool if p not in task.used), None)
            if phrase is None:
                # Use unused earlier alternatives after the long-task pool.
                phrase = next((p for pool in POOLS[task.language] for p in pool
                               if p not in task.used), None)
            if phrase is None:
                return  # Finite pool exhausted: never loop identical speech.
            task.count += 1
            task.used.add(phrase)
            task.pending = f'{task.task_id}:{task.count}'
            await self.send({'type': 'filler', 'task_id': task.task_id,
                             'turn_id': task.turn_id, 'language': task.language,
                             'utterance_id': task.pending, 'text': phrase})

    async def acknowledge(self, task_id, utterance_id, status):
        async with self.lock:
            task = self.tasks.get(task_id)
            if task and task.pending == utterance_id:
                task.pending = None
                task.last_end = self.clock()
                if status != 'ended':
                    task.state = 'cancelled'
                    if task.runner:
                        task.runner.cancel()

    async def stop(self, task_id, reason='answer_ready'):
        if reason not in STOP_STATES:
            raise ValueError('Invalid filler stop reason')
        runner = None
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                return
            task.state = reason
            task.pending = None
            runner = task.runner
            if runner:
                runner.cancel()
            await self.send({'type': 'filler_task_stopped', 'task_id': task_id,
                             'turn_id': task.turn_id, 'reason': reason})
        if runner:
            await asyncio.gather(runner, return_exceptions=True)

    async def stop_all(self, reason='cancelled'):
        for task_id in tuple(self.tasks):
            await self.stop(task_id, reason)

    def forget(self, task_id):
        task = self.tasks.get(task_id)
        if task and task.state != 'processing':
            self.tasks.pop(task_id, None)
