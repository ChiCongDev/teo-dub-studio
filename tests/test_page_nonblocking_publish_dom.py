"""Real publisher generators interleave in one isolated offline Chrome thread."""
import html
from pathlib import Path
import unittest
from unittest.mock import patch

from teodub.facebook_post_tasks import BrowserPostTasks
from tests import test_page_browser_dom as fixture


class NonblockingPublishDomTests(unittest.TestCase):
    setUpClass = fixture.BrowserDomTests.__dict__['setUpClass']
    tearDownClass = fixture.BrowserDomTests.__dict__['tearDownClass']
    draft = fixture.BrowserDomTests.draft
    prepare = fixture.BrowserDomTests.prepare
    saved = fixture.BrowserDomTests.saved
    composer_tab = fixture.BrowserDomTests.composer_tab

    def setUp(self):
        fixture.BrowserDomTests.setUp(self)
        self.context.set_offline(True)
        self.tasks = BrowserPostTasks()
        self.addCleanup(self.tasks.close)
        self.events = []
        budget = patch('teodub.facebook_web_post.PUBLISH_RESULT_SECONDS', 30)
        budget.start()
        self.addCleanup(budget.stop)

    def add_task(self, request, operation, post):
        iterator = getattr(self.publisher, operation+'_steps')(
            self.context, self.account['id'], self.target['id'], post['id'])
        self.tasks.add(request, iterator,
            scope=(self.account['id'], self.target['id'], post['id']), cancel=None,
            event=lambda kind,message,**extra:self.events.append((request,kind,extra)))

    def advance_until(self, predicate):
        for _ in range(60):
            if predicate():
                return
            self.tasks.step()
        self.fail('Cooperative browser operation did not reach its expected state: '+repr(self.events))

    def video(self, name, body):
        path = Path(self.temp.name)/(name+'.mp4')
        path.write_bytes(('Offline disposable video '+name).encode())
        return self.draft(body, [path])

    def route_published(self, reel_id, body):
        document = ('<meta charset="utf-8"><main><a href="https://www.facebook.com/profile.php?id=123">Page Demo</a>'
            '<div style="white-space:pre-wrap">'+html.escape(body)+'</div>'
            '<video src="blob:published-'+reel_id+'" width="120" height="120"></video></main>'
            '<script>Object.defineProperties(document.querySelector("video"),'
            '{duration:{value:30},videoWidth:{value:720},videoHeight:{value:1280}});</script>')
        self.context.route('**/reel/'+reel_id, lambda route:route.fulfill(content_type='text/html',body=document))

    def test_second_real_video_prepares_and_publishes_before_first_receipt(self):
        self.html = fixture.reel_fixture().replace(
            'Your post was published. <a href="https://www.facebook.com/123/videos/999">View video</a>',
            'Facebook is processing this video')
        first = self.video('first', 'Caption for first video')
        self.prepare(first)
        first_tab = self.composer_tab()
        self.add_task('publish A', 'publish', first)
        self.advance_until(lambda:self.tasks.tasks.get('publish A',{}).get('phase')=='verifying')
        self.assertEqual(self.saved(first)['state'], 'processing')
        self.assertFalse(self.tasks.busy)
        self.assertEqual((first_tab.evaluate('window.uploads'),first_tab.evaluate('window.posts')), (1,1))

        self.html = fixture.reel_fixture().replace(
            'https://www.facebook.com/123/videos/999', 'https://www.facebook.com/reel/222')
        second = self.video('second', 'Caption for second video')
        self.route_published('222', second['body'])
        self.add_task('prepare B', 'prepare', second)
        self.advance_until(lambda:'prepare B' not in self.tasks.tasks)
        second_tab = self.composer_tab()
        self.assertEqual(self.saved(second)['state'], 'review')
        self.assertEqual(self.saved(first)['state'], 'processing')
        self.assertIn('publish A', self.tasks.tasks)
        self.assertEqual(second_tab.evaluate('window.uploads'),1)
        self.assertIsNone(second_tab.evaluate('window.posts'))

        self.add_task('publish B', 'publish', second)
        self.advance_until(lambda:'publish B' not in self.tasks.tasks)
        self.assertEqual(self.saved(second)['state'], 'published')
        self.assertEqual(self.saved(first)['state'], 'processing')
        self.assertEqual((second_tab.evaluate('window.uploads'),second_tab.evaluate('window.posts')), (1,1))

        self.route_published('111', first['body'])
        first_tab.evaluate('''() => {const status=document.createElement('div');status.setAttribute('role','status');
            status.innerHTML='Your reel is published <a href="https://www.facebook.com/reel/111">View reel</a>';
            document.body.append(status);}''')
        self.advance_until(lambda:'publish A' not in self.tasks.tasks)
        self.assertEqual(self.saved(first)['state'], 'published')
        self.assertEqual((first_tab.evaluate('window.uploads'),first_tab.evaluate('window.posts')), (1,1))
        self.assertEqual(self.saved(first)['external_url'], 'https://www.facebook.com/reel/111')
        self.assertEqual(self.saved(second)['external_url'], 'https://www.facebook.com/reel/222')
        results = [(request,extra['result']['state']) for request,kind,extra in self.events if kind=='post_result']
        self.assertEqual(results,[('prepare B','review'),('publish B','published'),('publish A','published')])
        self.assertFalse(any(kind=='post_error' for _,kind,_ in self.events))


if __name__ == '__main__':
    unittest.main()
