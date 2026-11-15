import pathlib, re, subprocess, unittest
D = pathlib.Path(__file__).resolve().parents[1]

class QaCardTests(unittest.TestCase):
    def test_qa_card(self):
        js = (D / 'ui/app.js').read_text()
        fn = re.search(r'// QA-CARD-START(.*?)// QA-CARD-END', js, re.S).group(1)
        code = 'function esc(s){return s}function mdInline(s){return s}' + fn + r'''
var h = qaCard("Q: Como?\n## Windows/PC\n1. um\n2. dois\n## App\n1. tres");
if (!/qa-q">Como\?/.test(h) || (h.match(/<li>/g)||[]).length !== 3 || (h.match(/type="radio"/g)||[]).length !== 2 || !/qa-pane-1/.test(h)) { console.error(h); process.exit(1); }'''
        r = subprocess.run(['node', '-e', code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        subprocess.run(['node', '--check', str(D / 'ui/app.js')], check=True)

if __name__ == '__main__':
    unittest.main()
