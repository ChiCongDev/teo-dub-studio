"""Offline publishing tests: temporary registry, synthetic keys and mocked Graph transport."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

import requests

from teodub.common import Cancelled
from teodub.facebook_accounts import (FacebookAccounts, facebook_browser_url, facebook_post_url,
                                      facebook_url, META_APPS, META_EXPLORER)
from teodub.meta_page_api import (MetaError, MetaPageClient, MultipartStream, PageApiService,
                                  PageCredentials, DEFAULT_API_VERSION)
from teodub.page_activity import PageActivity
from teodub.pipeline import _job_lock


class ActivityFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = FacebookAccounts(self.directory)
        self.account = self.accounts.add_account('Synthetic A')['id']
        self.page = self.accounts.save_page(self.account, 'Synthetic Page', 'facebook.com/syntheticpage', '100')['id']
        self.other = self.accounts.add_account('Synthetic B')['id']
        self.other_page = self.accounts.save_page(self.other, 'Other Page', 'facebook.com/otherpage', '200')['id']
        self.store = PageActivity(self.directory)

    def draft(self, *paths):
        return self.store.save_draft(self.account, self.page, 'Bài thử ngoại tuyến', paths)

    def file(self, name='ảnh.png', content=b'synthetic content'):
        path = self.directory/name
        path.write_bytes(content)
        return path


class PageActivityTests(ActivityFixture):
    def test_notes_posts_reopen_scoped_to_page_and_account(self):
        self.store.save_details(self.account, self.page, 'Câu cá', 'Ghi chú riêng')
        draft = self.draft()
        reopened = PageActivity(self.directory)
        self.assertEqual(reopened.details(self.account, self.page)['topic'], 'Câu cá')
        self.assertEqual(reopened.posts(self.account, self.page)[0]['body'], draft['body'])
        self.assertEqual(reopened.posts(self.other, self.other_page), [])
        with self.assertRaises(ValueError):
            reopened.post(self.other, self.other_page, draft['id'])
        with self.assertRaises(ValueError):
            reopened.save_details(self.other, self.page, '', '')

    def test_busy_and_unknown_posts_cannot_edit_archive_or_claim_twice(self):
        row = self.draft()
        self.store.transition(self.account, self.page, row['id'], 'preparing', 'claim', expected={'draft'})
        for state in ('preparing', 'submitting', 'processing', 'unknown'):
            self.store.transition(self.account, self.page, row['id'], state, 'state')
            with self.assertRaises(ValueError):
                self.store.save_draft(self.account, self.page, 'Đổi lời', post_id=row['id'])
            with self.assertRaises(ValueError):
                self.store.archive(self.account, self.page, row['id'])
            with self.assertRaises(ValueError):
                self.store.transition(self.account, self.page, row['id'], 'preparing', '', expected={'draft'})
        self.assertEqual(self.store.post(self.account, self.page, row['id'])['body'], row['body'])

    def test_concurrent_claim_has_one_winner(self):
        row = self.draft()
        barrier = threading.Barrier(2)
        def claim():
            barrier.wait(timeout=3)
            try:
                self.store.transition(self.account, self.page, row['id'], 'preparing', 'claim', expected={'draft'})
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(self.store.post(self.account, self.page, row['id'])['attempt'], 1)

    def test_media_validation_and_changed_files_keep_draft(self):
        image, video = self.file(), self.file('video.mp4')
        for paths in ((image, video), (video, video), (image,)*11, (self.file('x.txt'),)):
            with self.assertRaises(ValueError):
                self.draft(*paths)
        row = self.draft(image)
        image.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.store.verify_media(row)
        refreshed = self.store.save_draft(self.account, self.page, row['body'], [image], post_id=row['id'])
        self.store.verify_media(refreshed)
        self.assertEqual(refreshed['state'], 'draft')

    def test_archiving_keeps_history_and_attached_files(self):
        image = self.file()
        row = self.draft(image)
        self.store.archive(self.account, self.page, row['id'])
        self.assertEqual(self.store.posts(self.account, self.page), [])
        self.assertEqual(len(self.store.posts(self.account, self.page, include_archived=True)), 1)
        self.assertTrue(image.exists())
        self.assertEqual(len(self.store.events(self.account, self.page, row['id'])), 2)

    def test_observed_zero_is_preserved_missing_is_not_zero(self):
        self.store.observe(self.account, self.page, {'name':'API name','likes':0,'followers':None,'access_token':'discard'})
        result = self.store.observations(self.account, self.page)[0]
        self.assertEqual(result['likes'], 0)
        self.assertNotIn('followers', result)
        self.assertNotIn('access_token', result)

    def test_published_requires_permalink_and_preserves_first_timestamp(self):
        row = self.draft()
        with self.assertRaises(ValueError):
            self.store.transition(self.account, self.page, row['id'], 'published', 'no evidence')
        row = self.store.transition(self.account, self.page, row['id'], 'published', 'evidence',
             external_id='100_77', external_url='https://facebook.com/permalink.php?story_fbid=77&id=100&access_token=discard')
        updated = self.store.transition(self.account, self.page, row['id'], 'published', 'update', external_url=row['external_url'])
        self.assertEqual(updated['published_at'], row['published_at'])
        self.assertNotIn('discard', updated['external_url'])

    def test_developer_urls_only_allowed_for_browser_not_saved_pages(self):
        for url in (META_APPS, META_EXPLORER):
            self.assertEqual(facebook_browser_url(url), url)
            with self.assertRaises(ValueError):
                self.accounts.save_page_link(self.account, url)
        for bad in ('https://developers.facebook.com/evil/', META_APPS+'?access_token=discard',
                    'https://developers.facebook.com.evil/apps/', 'https://evil/','https://x@developers.facebook.com/apps/'):
            with self.assertRaises(ValueError):
                facebook_browser_url(bad)

    def test_permalink_ids_survive_browser_navigation(self):
        value = 'https://www.facebook.com/story.php?story_fbid=77&id=100&ref=x'
        self.assertEqual(facebook_post_url(value), 'https://www.facebook.com/story.php?story_fbid=77&id=100')
        self.assertEqual(facebook_url(value), facebook_post_url(value))
        for bad in ('https://evil/posts/77','https://www.facebook.com/watch/?v=abc'):
            with self.assertRaises(ValueError):
                facebook_post_url(bad)


class MetaTransportTests(unittest.TestCase):
    def response(self, data, status=200):
        response = Mock(status_code=status)
        response.json.return_value = data
        return response

    def client(self, response=None):
        session = Mock()
        session.request.return_value = response or self.response({'id':'100'})
        return MetaPageClient('synthetic-key', session=session), session

    def test_get_bearer_not_in_url_redirects_disabled_response_closed(self):
        response = self.response({'id':'100'})
        client, session = self.client(response)
        client.request('GET','100',fields='id,name')
        args, kwargs = session.request.call_args
        self.assertEqual(args, ('GET', 'https://graph.facebook.com/'+DEFAULT_API_VERSION+'/100'))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer synthetic-key')
        self.assertEqual(kwargs['params'], {'fields':'id,name'})
        self.assertFalse(kwargs['allow_redirects'])
        response.close.assert_called_once()

    def test_no_retry_ambiguous_post_but_get_is_not_send_failure(self):
        client, session = self.client()
        session.request.side_effect = requests.Timeout('synthetic-key should not leak')
        for method, uncertain in (('POST',True), ('GET',False)):
            with self.assertRaises(MetaError) as raised:
                client.request(method, '100', edge='feed')
            self.assertEqual(raised.exception.uncertain, uncertain)
            self.assertNotIn('synthetic-key', str(raised.exception))
        self.assertEqual(session.request.call_count, 2)

    def test_graph_error_redaction_and_rejected_vs_uncertain(self):
        for status, data, uncertain in ((400, {'error':{'code':190,'message':'expired synthetic-key'}}, False),
                                         (503, {'error':{'code':2,'message':'server error'}}, True),
                                         (302, {'id':'100_77'}, True), (200, [], True)):
            client, session = self.client(self.response(data,status))
            with self.assertRaises(MetaError) as raised:
                client.request('POST','100',edge='feed')
            self.assertEqual(raised.exception.uncertain, uncertain)
            self.assertNotIn('synthetic-key', str(raised.exception))
            session.request.assert_called_once()

    def test_invalid_json_and_destination_blocked(self):
        response = self.response({})
        response.json.side_effect = ValueError('raw secret')
        client, session = self.client(response)
        with self.assertRaises(MetaError) as raised:
            client.request('POST','100')
        self.assertTrue(raised.exception.uncertain)
        response.close.assert_called_once()
        for method, node, edge in (('DELETE','100',''), ('GET','https://evil/',''), ('POST','100','accounts')):
            with self.assertRaises(ValueError):
                client.request(method,node,edge=edge)
        session.request.assert_called_once()

    def test_identity_requires_page_token_and_correct_id(self):
        client, session = self.client()
        for data in ({'id':'100','name':'Person'}, {'id':'200','name':'Page','category':'Test'}):
            session.request.return_value = self.response(data)
            with self.assertRaises(ValueError):
                client.verify_page('100')
        session.request.return_value = self.response({'id':'100','name':'Page','category':'Test'})
        self.assertEqual(client.verify_page('100')['id'], '100')

    def test_recent_posts_discards_wrong_page_and_auth_payload(self):
        client, session = self.client(self.response({'data':[{'id':'100_77','message':'post','access_token':'discard'},
             {'id':'200_99','message':'wrong account'}], 'paging':{'next':'discard secret'}}))
        rows = client.recent_posts('100')
        self.assertEqual(len(rows), 1)
        self.assertNotIn('discard', repr(rows))
        self.assertIsNone(rows[0]['likes'])

    def test_multipart_is_streamed_exact_length_and_cancellable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'private name.mp4'
            path.write_bytes(b'z'*180000)
            cancel = threading.Event()
            with path.open('rb') as handle:
                body = MultipartStream({'description':'Tiếng Việt'},handle,str(path),cancel=cancel)
                prepared = requests.Request('POST','https://graph.facebook.com/v26.0/100/videos',data=body,
                    headers={'Content-Type':body.content_type,'Content-Length':str(len(body))}).prepare()
                self.assertIs(prepared.body, body)
                self.assertEqual(prepared.headers['Content-Length'], str(len(body)))
                self.assertNotIn('Transfer-Encoding', prepared.headers)
                blocks = []
                while block := body.read():
                    self.assertLessEqual(len(block),65536)
                    blocks.append(block)
                data = b''.join(blocks)
                self.assertEqual(len(data),len(body))
                self.assertIn(b'name="source"; filename="upload.mp4"',data)
                self.assertNotIn(str(path).encode(),data)
                self.assertIn(b'z'*180000,data)
                cancel.set()
                with self.assertRaises(Cancelled):
                    body.read(1)


class FakeVault:
    def __init__(self):
        self.values = {}
    def save(self, account, page, token):
        import uuid
        ref = uuid.uuid4().hex
        self.values[ref] = (account,page,token)
        return ref
    def load(self, account, page, ref):
        a,p,token = self.values[ref]
        if (a,p) != (account,page):
            raise ValueError('Wrong scope')
        return token
    def remove(self, ref):
        self.values.pop(ref,None)


class PageApiServiceTests(ActivityFixture):
    def setUp(self):
        super().setUp()
        self.remote = Mock()
        self.remote.verify_page.return_value = {'id':'100','name':'Verified Synthetic Page','category':'Test'}
        self.remote.page_info.return_value = {'name':'Verified Synthetic Page','likes':0,'followers':20}
        self.remote.recent_posts.return_value = []
        self.remote.request.return_value = {'id':'100_77'}
        self.remote.post_info.return_value = {'id':'100_77','is_published':True,
            'permalink_url':'https://www.facebook.com/syntheticpage/posts/77', 'likes':{'summary':{'total_count':0}}}
        self.factory = Mock(return_value=self.remote)
        self.vault = FakeVault()
        self.service = PageApiService(self.directory,client_factory=self.factory,credentials=self.vault)
        self.service.connect(self.account,self.page,'100','synthetic-key','v26.0')
        self.remote.reset_mock()
        self.factory.reset_mock()

    def publish(self, row):
        return self.service.publish(self.account,self.page,row['id'])

    def test_text_post_is_sent_once_verified_and_can_read_without_reposting(self):
        row = self.draft()
        result = self.publish(row)
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['external_id'],'100_77')
        self.assertEqual(result['metrics']['likes'],0)
        self.assertNotIn('comments',result['metrics'])
        self.remote.verify_page.assert_called_once_with('100')
        self.assertEqual(self.remote.request.call_args.kwargs['edge'],'feed')
        self.service.check_post(self.account,self.page,row['id'])
        self.remote.request.assert_called_once()
        with self.assertRaises(ValueError):
            self.publish(row)
        self.remote.request.assert_called_once()
        self.assertNotIn('synthetic-key', self.store.path.read_bytes().decode('latin1'))

    def test_images_upload_unpublished_then_one_combined_feed(self):
        row = self.draft(self.file('a.png'),self.file('b.png'))
        self.remote.request.side_effect = [{'id':'11'},{'id':'12'},{'id':'100_77'}]
        self.assertEqual(self.publish(row)['state'],'published')
        calls = self.remote.request.call_args_list
        self.assertEqual([call.kwargs['edge'] for call in calls], ['photos','photos','feed'])
        self.assertTrue(all(call.kwargs['data']['published']=='false' for call in calls[:2]))
        self.assertEqual(json.loads(calls[2].kwargs['data']['attached_media']),[{'media_fbid':'11'},{'media_fbid':'12'}])

    def test_rejected_feed_retains_uploaded_images_for_explicit_retry(self):
        row = self.draft(self.file())
        self.remote.request.side_effect = [{'id':'11'},MetaError('permission denied',code=200)]
        with self.assertRaises(MetaError):
            self.publish(row)
        saved = self.store.post(self.account,self.page,row['id'])
        self.assertEqual(saved['state'],'failed')
        self.assertEqual(saved['remote_assets'],['11'])
        self.remote.request.reset_mock(side_effect=True)
        self.remote.request.return_value = {'id':'100_77'}
        self.assertEqual(self.publish(row)['state'],'published')
        self.remote.request.assert_called_once()
        self.assertEqual(self.remote.request.call_args.kwargs['edge'],'feed')

    def test_ambiguous_response_keeps_unknown_and_prevents_resend(self):
        row = self.draft()
        self.remote.request.side_effect = MetaError('network timeout',uncertain=True)
        with self.assertRaises(MetaError):
            self.publish(row)
        self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'unknown')
        with self.assertRaises(ValueError):
            self.publish(row)
        self.remote.request.assert_called_once()

    def test_post_id_is_kept_when_result_read_fails(self):
        row = self.draft()
        self.remote.post_info.side_effect = MetaError('read timeout')
        saved = self.publish(row)
        self.assertEqual(saved['state'],'processing')
        self.assertEqual(saved['external_id'],'100_77')
        self.remote.request.assert_called_once()
        with self.assertRaises(ValueError):
            self.publish(row)

    def test_video_processing_complete_and_error_never_upload_again(self):
        row = self.draft(self.file('video.mp4'))
        self.remote.request.return_value = {'id':'77'}
        self.remote.post_info.return_value = {'id':'77','published':True,'status':{'video_status':'processing'},
           'permalink_url':'https://www.facebook.com/watch/?v=77'}
        self.assertEqual(self.publish(row)['state'],'processing')
        self.assertEqual(self.remote.request.call_args.kwargs['edge'],'videos')
        self.remote.post_info.return_value['status'] = {'video_status':'error'}
        self.assertEqual(self.service.check_post(self.account,self.page,row['id'])['state'],'unknown')
        self.remote.post_info.return_value['status'] = {'publishing_phase':{'status':'complete'}}
        result = self.service.check_post(self.account,self.page,row['id'])
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['external_url'],'https://www.facebook.com/watch?v=77')
        self.remote.request.assert_called_once()

    def test_preflight_media_or_identity_failure_has_no_post(self):
        image = self.file()
        row = self.draft(image)
        image.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.publish(row)
        self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'draft')
        row = self.draft()
        self.remote.verify_page.side_effect = ValueError('wrong page')
        with self.assertRaises(ValueError):
            self.publish(row)
        self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'failed')
        self.remote.request.assert_not_called()

    def test_expired_token_marks_reconnect_keeps_saved_info(self):
        self.service.refresh_page(self.account,self.page)
        self.remote.recent_posts.side_effect = MetaError('expired',code=190)
        result = self.service.refresh_page(self.account,self.page)
        self.assertIn('expired',result['message'])
        self.assertEqual(self.store.connection(self.account,self.page)['status'],'reconnect')
        self.remote.page_info.side_effect = MetaError('expired',code=190)
        with self.assertRaises(MetaError):
            self.service.refresh_page(self.account,self.page)
        self.assertEqual(self.store.observations(self.account,self.page)[0]['followers'],20)

    def test_wrong_id_and_account_do_not_replace_credentials(self):
        old = self.store.connection(self.account,self.page)
        for account,page,page_id in ((self.account,self.page,'200'),(self.other,self.page,'100')):
            with self.assertRaises(ValueError):
                self.service.connect(account,page,page_id,'other-key','v26.0')
        self.assertEqual(self.store.connection(self.account,self.page)['credential_ref'],old['credential_ref'])
        self.factory.assert_not_called()

    def test_orphan_submission_becomes_unknown_only_after_lock_released(self):
        row = self.draft()
        self.store.transition(self.account,self.page,row['id'],'submitting','sending')
        directory = self.accounts.directory/'post-locks'/row['id']
        directory.mkdir(parents=True)
        with _job_lock(directory):
            with self.assertRaises(ValueError):
                self.service.check_post(self.account,self.page,row['id'])
            self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'submitting')
        with self.assertRaises(ValueError):
            self.service.check_post(self.account,self.page,row['id'])
        self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'unknown')
        self.remote.request.assert_not_called()

    def test_disconnect_keeps_drafts_and_observations(self):
        row = self.draft()
        self.service.refresh_page(self.account,self.page)
        self.service.disconnect(self.account,self.page)
        self.assertIsNone(self.store.connection(self.account,self.page))
        self.assertEqual(self.vault.values,{})
        self.assertEqual(self.store.post(self.account,self.page,row['id'])['state'],'draft')
        self.assertTrue(self.store.observations(self.account,self.page))

    def test_draft_before_connection_binds_page_and_retry_cannot_switch_target(self):
        account = self.accounts.add_account('Synthetic unconnected')['id']
        page = self.accounts.save_page(account, 'Unconnected Page', 'facebook.com/unconnected')['id']
        row = self.store.save_draft(account,page,'Bài thử ngoại tuyến')
        self.assertEqual(row['target_id'],'')
        self.remote.verify_page.return_value = dict(id='300',name='Synthetic Page 300',category='Test')
        self.service.connect(account,page,'300','synthetic-300','v26.0')
        self.remote.request.side_effect = MetaError('rejected',code=200)
        with self.assertRaises(MetaError):
            self.service.publish(account,page,row['id'])
        self.assertEqual(self.store.post(account,page,row['id'])['target_id'],'300')
        self.accounts.save_page(account,'Unconnected Page','facebook.com/unconnected','400',record_id=page)
        self.remote.verify_page.return_value = dict(id='400',name='Synthetic Page 400',category='Test')
        self.service.connect(account,page,'400','synthetic-400','v26.0')
        self.remote.request.reset_mock()
        with self.assertRaises(ValueError):
            self.service.publish(account,page,row['id'])
        self.remote.request.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
class WindowsCredentialTests(unittest.TestCase):
    def test_real_encryption_synthetic_key_bound_to_account_and_page(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = PageCredentials(directory)
            ref = vault.save('a'*32,'b'*32,'synthetic-not-a-real-api-key')
            self.assertNotIn(b'synthetic-not-a-real-api-key',vault.path(ref).read_bytes())
            self.assertEqual(vault.load('a'*32,'b'*32,ref),'synthetic-not-a-real-api-key')
            with self.assertRaises(ValueError):
                vault.load('c'*32,'b'*32,ref)
            with self.assertRaises(ValueError):
                vault.path('../bad')
            vault.remove(ref)
            self.assertFalse(vault.path(ref).exists())


if __name__ == '__main__':
    unittest.main()
