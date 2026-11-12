import unittest
import base64
from array import array
from backend.services.voice_fillers import FillerScheduler, STOP_STATES
from backend.services.voice_language_id import AudioLanguageWindow


class FillerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0
        self.events = []
        async def send(event):
            self.events.append(event)
        self.scheduler = FillerScheduler(send, clock=lambda: self.now)

    async def test_timings_long_running_no_repeat_and_normalized_language(self):
        await self.scheduler.start('task', 'turn', 'ta-IN', run=False)
        for instant in (2.49, 2.5, 10, 16, 24, 33, 42):
            self.now = instant
            await self.scheduler.tick('task')
            task = self.scheduler.tasks['task']
            if task.pending:
                await self.scheduler.acknowledge('task', task.pending, 'ended')
        fillers = [e for e in self.events if e['type'] == 'filler']
        self.assertEqual(len(fillers), 6)
        self.assertEqual(len({e['text'] for e in fillers}), 6)
        self.assertTrue(all(e['language'] == 'ta' for e in fillers))

    async def test_generation_lead_requests_audio_before_spoken_milestone(self):
        self.scheduler.lead_time = 1.0
        await self.scheduler.start('task', 'turn', 'en', run=False)
        self.now = 1.49
        await self.scheduler.tick('task')
        self.assertFalse(any(e['type'] == 'filler' for e in self.events))
        self.now = 1.5
        await self.scheduler.tick('task')
        self.assertEqual(sum(e['type'] == 'filler' for e in self.events), 1)

    async def test_completion_before_second_deadline(self):
        await self.scheduler.start('task', 'turn', 'si', run=False)
        self.now = 9.1
        await self.scheduler.stop('task', 'answer_ready')
        self.now = 10
        await self.scheduler.tick('task')
        self.assertFalse(any(e['type'] == 'filler' for e in self.events))

    async def test_unacknowledged_playback_never_overlaps(self):
        await self.scheduler.start('task', 'turn', 'en-US', run=False)
        self.now = 2.5
        await self.scheduler.tick('task')
        self.now = 33
        await self.scheduler.tick('task')
        self.assertEqual(sum(e['type'] == 'filler' for e in self.events), 1)

    async def test_unknown_language_and_duplicate_start(self):
        self.assertIsNone(await self.scheduler.start('unknown', 'turn', None, run=False))
        first = await self.scheduler.start('task', 'turn', 'ta', run=False)
        second = await self.scheduler.start('task', 'other-turn', 'en', run=False)
        self.assertIs(first, second)
        self.assertEqual(first.language, 'ta')

    async def test_every_terminal_state_cancels_runner(self):
        for reason in STOP_STATES:
            task = await self.scheduler.start(reason, 'turn', 'si')
            await self.scheduler.stop(reason, reason)
            self.assertTrue(task.runner.done())
            self.now += 100
            await self.scheduler.tick(reason)
        self.assertFalse(any(e['type'] == 'filler' for e in self.events))

    async def test_old_task_ack_does_not_release_new_task(self):
        await self.scheduler.start('old', 'one', 'ta', run=False)
        await self.scheduler.stop('old', 'user_interrupt')
        await self.scheduler.start('new', 'two', 'en', run=False)
        self.now = 3
        await self.scheduler.tick('new')
        pending = self.scheduler.tasks['new'].pending
        await self.scheduler.acknowledge('old', pending, 'ended')
        self.assertEqual(self.scheduler.tasks['new'].pending, pending)

    async def test_single_loud_mic_packet_does_not_cancel_filler(self):
        window = AudioLanguageWindow()
        voiced = base64.b64encode(array('h', [3000] * 4096).tobytes()).decode()
        silent = base64.b64encode(bytes(8192)).decode()
        self.assertFalse(window.feed(voiced))
        self.assertTrue(window.feed(voiced))
        self.assertFalse(window.feed(voiced))
        for _ in range(3):
            window.feed(silent)
        self.assertFalse(window.feed(voiced))
