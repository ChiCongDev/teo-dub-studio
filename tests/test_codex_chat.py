import io
import json
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

from teodub import codex_chat as chat


CHAT_ID = '01a12053-b14d-7811-b8b9-48760720e7ef'
OTHER_ID = '01a12053-b14d-7811-b8b9-48760720e7ee'


def lines(*values):
    return b''.join((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8') for value in values)


def reply(thread):
    return lines({'id': 1, 'result': {}}, {'id': 2, 'result': {'thread': thread}})


def thread(turns=None, **extra):
    return {'id': CHAT_ID, 'name': 'Phát triển page câu cá', 'cwd': 'C:/test',
            'updatedAt': 1791544244, 'status': {'type': 'notLoaded'},
            'turns': [] if turns is None else turns, **extra}


def answer(text, phase='final_answer', **extra):
    return {'type': 'agentMessage', 'text': text, 'phase': phase, **extra}


class _RecordingPipe(io.BytesIO):
    def close(self):
        self.was_closed = True


class _WaitingPipe:
    def __init__(self):
        self.released = threading.Event()

    def readline(self, limit):
        self.released.wait(2)
        return b''

    def close(self):
        self.released.set()


class _Process:
    def __init__(self, output=b'', hang=False, needs_kill=False):
        self.stdin = _RecordingPipe()
        self.stdout = _WaitingPipe() if hang else io.BytesIO(output)
        self.hang = hang
        self.needs_kill = needs_kill
        self.terminated = False
        self.killed = False

    def wait(self, timeout):
        if self.hang and not self.killed and (not self.terminated or self.needs_kill):
            raise subprocess.TimeoutExpired('only-test-reader-child', timeout)
        return 0

    def terminate(self):
        self.terminated = True
        if isinstance(self.stdout, _WaitingPipe) and not self.needs_kill:
            self.stdout.released.set()

    def kill(self):
        self.killed = True
        if isinstance(self.stdout, _WaitingPipe):
            self.stdout.released.set()

    def requests(self):
        return [json.loads(line) for line in self.stdin.getvalue().splitlines()]


class CodexChatTests(unittest.TestCase):
    def read(self, data, **kwargs):
        process = _Process(reply(data))
        with patch.object(chat, 'find_codex', return_value='codex-native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            result = chat.read_thread(CHAT_ID, **kwargs)
        return result, process

    def test_real_schema_only_completed_explicit_finals_in_stored_order(self):
        data = thread([
            {'id': 'old', 'status': 'completed', 'items': [
                {'type': 'userMessage', 'text': 'do not import'},
                answer('comment', 'commentary'), answer('old final'),
                {'type': 'reasoning', 'content': 'not public'},
                {'type': 'mcpToolCall', 'output': 'not public'},
                answer('legacy missing phase', None),
            ]},
            {'id': 'active', 'status': 'inProgress', 'items': [answer('unfinished final')]},
            {'id': 'failed', 'status': 'failed', 'items': [answer('failed final')]},
            {'id': 'interrupted', 'status': 'interrupted', 'items': [answer('interrupted final')]},
            {'id': 'new', 'status': 'completed', 'items': [
                answer('new final'), answer('still in progress', status='inProgress'),
                answer('   '), {'type': 'functionCallOutput', 'output': 'fake plan'},
            ]},
        ])
        result, process = self.read(data)
        self.assertEqual(result['id'], CHAT_ID)
        self.assertEqual(result['title'], 'Phát triển page câu cá')
        self.assertEqual(result['status'], 'notLoaded')
        self.assertEqual(result['messages'], [
            {'turn_id': 'old', 'text': 'old final', 'phase': 'final_answer', 'turn_status': 'completed'},
            {'turn_id': 'new', 'text': 'new final', 'phase': 'final_answer', 'turn_status': 'completed'},
        ])
        self.assertFalse(process.terminated)

    def test_reader_never_resumes_or_invokes_model_and_strips_api_env_keys(self):
        process = _Process(reply(thread()))
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process) as launch, \
                patch.dict(chat.os.environ, {'OPENAI_API_KEY': 'separate-paid-api', 'CODEX_API_KEY': 'separate-paid-api'}):
            chat.read_thread(CHAT_ID)
        sent = process.requests()
        self.assertEqual([item['method'] for item in sent], ['initialize', 'initialized', 'thread/read'])
        self.assertEqual(sent[-1]['params'], {'threadId': CHAT_ID, 'includeTurns': True})
        self.assertEqual(launch.call_args.args[0][:3], ['native.exe', 'app-server', '--stdio'])
        self.assertFalse(launch.call_args.kwargs['shell'])
        self.assertNotIn('OPENAI_API_KEY', launch.call_args.kwargs['env'])
        self.assertNotIn('CODEX_API_KEY', launch.call_args.kwargs['env'])
        self.assertIs(launch.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_list_uses_state_db_only_and_returns_small_summaries(self):
        data = thread()
        data['preview'] = 'private preview is not used because name exists'
        data['turns'] = [{'private': 'never returned'}]
        process = _Process(lines({'id': 1, 'result': {}}, {'id': 2, 'result': {'data': [data], 'nextCursor': None}}))
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            result = chat.list_threads('câu cá', 30)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]), {'id', 'title', 'status', 'updated_at', 'cwd'})
        sent = process.requests()
        self.assertEqual([item['method'] for item in sent], ['initialize', 'initialized', 'thread/list'])
        self.assertTrue(sent[-1]['params']['useStateDbOnly'])
        self.assertEqual(sent[-1]['params']['searchTerm'], 'câu cá')
        self.assertEqual(sent[-1]['params']['sourceKinds'], ['cli', 'vscode', 'appServer'])

    def test_wrong_thread_does_not_return_any_text(self):
        with self.assertRaisesRegex(chat.CodexChatError, 'chat khác'):
            self.read(thread([{'id': 't', 'status': 'completed', 'items': [answer('wrong chat plan')]}], id=OTHER_ID))

    def test_invalid_thread_ids_never_spawn(self):
        for identifier in (None, '', '../secret', 'https://chatgpt.com/c/abc', CHAT_ID + '\n', CHAT_ID.upper()):
            with self.subTest(identifier=identifier), patch.object(chat.subprocess, 'Popen') as launch:
                with self.assertRaises(chat.CodexChatError):
                    chat.read_thread(identifier)
                launch.assert_not_called()

    def test_unsupported_method_error_is_actionable_and_sanitized(self):
        process = _Process(lines({'id': 1, 'result': {}}, {'id': 2, 'error': {
            'code': -32601, 'message': 'secret/local/auth/location'}}))
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(chat.CodexChatError, 'Cập nhật Codex') as failure:
                chat.read_thread(CHAT_ID)
        self.assertNotIn('secret', str(failure.exception))

    def test_malformed_responses_and_unexpected_request_rejected(self):
        for bad in (b'not-json\n', lines([]), lines({'id': 99, 'result': {}}),
                    lines({'id': 1, 'method': 'item/commandExecution/requestApproval'}),
                    lines({'id': 1, 'result': []})):
            process = _Process(bad)
            with self.subTest(bad=bad), patch.object(chat, 'find_codex', return_value='native.exe'), \
                    patch.object(chat.subprocess, 'Popen', return_value=process):
                with self.assertRaisesRegex(chat.CodexChatError, 'định dạng'):
                    chat.read_thread(CHAT_ID)
            self.assertEqual([request['method'] for request in process.requests()], ['initialize'])
            self.assertTrue(process.stdin.was_closed)

    def test_non_json_notification_cannot_supply_final_answers(self):
        output = lines({'method': 'item/agentMessage/delta', 'params': {'text': 'ignore'}},
                       {'id': 1, 'result': {}}, {'id': 2, 'result': {'thread': thread()}})
        process = _Process(output)
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            self.assertEqual(chat.read_thread(CHAT_ID)['messages'], [])

    def test_timeout_terminates_only_owned_child_without_inference(self):
        owned = _Process(hang=True)
        unrelated = Mock()
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=owned):
            with self.assertRaisesRegex(chat.CodexChatError, 'thời gian chờ'):
                chat.read_thread(CHAT_ID, timeout=0.01)
        self.assertTrue(owned.terminated)
        self.assertFalse(owned.killed)
        self.assertEqual([request['method'] for request in owned.requests()], ['initialize'])
        self.assertEqual(unrelated.mock_calls, [])

    def test_close_kills_only_unresponsive_owned_child(self):
        process = _Process(hang=True, needs_kill=True)
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            with self.assertRaises(chat.CodexChatError):
                chat.read_thread(CHAT_ID, timeout=0.01)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_output_limit_rejects_whole_response_without_import(self):
        process = _Process(reply(thread([{'id': 't', 'status': 'completed', 'items': [answer('x' * 2000)]}])))
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat, 'OUTPUT_LIMIT', 128), \
                patch.object(chat.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(chat.CodexChatError, 'giới hạn đọc'):
                chat.read_thread(CHAT_ID)

    def test_malformed_turns_and_final_text_fail_closed(self):
        for data in (thread(turns='bad'), thread([None]),
                     thread([{'id': 't', 'status': 'completed', 'items': 'bad'}]),
                     thread([{'id': 't', 'status': 'completed', 'items': [answer({'not': 'text'})]}])):
            with self.subTest(data=data), self.assertRaises(chat.CodexChatError):
                self.read(data)

    def test_no_generic_rpc_write_operations(self):
        client = chat._ReadClient()
        for method in ('turn/start', 'thread/start', 'thread/resume', 'thread/fork', 'account/logout', 'config/write'):
            with self.subTest(method=method), self.assertRaisesRegex(chat.CodexChatError, 'chỉ cho phép đọc'):
                client._request(method, {})

    def test_bad_list_and_timeout_inputs_do_not_start_process(self):
        with patch.object(chat.subprocess, 'Popen') as launch:
            for kwargs in ({'limit': 0}, {'limit': True}, {'limit': 101}, {'search_term': None}, {'search_term': 'x' * 201}, {'timeout': float('inf')}, {'timeout': 0}, {'timeout': True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(chat.CodexChatError):
                    chat.list_threads(**kwargs)
            launch.assert_not_called()

    def test_child_launch_failure_is_actionable_without_private_path(self):
        with patch.object(chat, 'find_codex', return_value='native.exe'), \
                patch.object(chat.subprocess, 'Popen', side_effect=OSError('private/config/path')):
            with self.assertRaisesRegex(chat.CodexChatError, 'Không mở được') as failure:
                chat.read_thread(CHAT_ID)
        self.assertNotIn('private', str(failure.exception))

    def test_active_chat_snapshot_does_not_import_current_partial_turn(self):
        data = thread([
            {'id': 'finished', 'status': 'completed', 'items': [answer('approved final')]},
            {'id': 'running', 'status': 'inProgress', 'items': [answer('partial plan')]},
        ], status={'type': 'active', 'activeFlags': []})
        result, process = self.read(data)
        self.assertEqual(result['status'], 'active')
        self.assertEqual([item['text'] for item in result['messages']], ['approved final'])
        self.assertEqual(process.requests()[-1]['method'], 'thread/read')


if __name__ == '__main__':
    unittest.main()
