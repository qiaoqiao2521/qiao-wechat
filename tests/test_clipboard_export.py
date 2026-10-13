import pytest
from bs4 import BeautifulSoup

from qiao_wechat.services.clipboard_export import prepare_clipboard_export
from qiao_wechat.services.wechat_export import semantic_snapshot


def test_title_image_and_caption_keep_inline_styles_without_preview_shell():
    source = '<section style="color:#123456"><h1 style="font-size:24px">自有标题</h1><p>短段落。</p><img data-src="https://mmbiz.qpic.cn/self/0" alt="解释图" style="width:100%;height:auto"><p style="font-size:14px">图解说明</p></section>'
    result = prepare_clipboard_export(source)
    soup = BeautifulSoup(result['html'], 'html.parser')
    assert soup.h1['style'] == 'font-size:24px'
    assert soup.img['src'] == 'https://mmbiz.qpic.cn/self/0'
    assert '自有标题\n' in result['plain_text']
    assert '图解说明' in result['plain_text']
    assert not soup.find(['html', 'head', 'body', 'button'])
    assert semantic_snapshot(result['html'])['text_sha256'] == semantic_snapshot(source)['text_sha256']


def test_copy_export_uses_visible_full_source_urls_and_preserves_code():
    source = '<p><strong>hello</strong> <em>world</em> <a href="https://example.com/source">来源</a></p><pre><code>if ok:\n    run()\n\n    return ok</code></pre>'
    result = prepare_clipboard_export(source)
    assert 'https://example.com/source' in result['plain_text']
    assert BeautifulSoup(result['html'], 'html.parser').code.get_text() == 'if ok:\n    run()\n\n    return ok'
    assert 'hello world' in result['plain_text']
    assert 'if ok:\n    run()\n\n    return ok' in result['plain_text']
    assert prepare_clipboard_export(result['html'])['html'] == result['html']


@pytest.mark.parametrize('content', [
    '<p onclick="run()">正文</p>', '<svg><text>互动</text></svg>',
    '<script>run()</script><p>正文</p>', '<iframe src="https://example.com"></iframe>',
    '<p style="background:url(https://example.com/bg.png)">正文</p>',
    '<p style="position:fixed">浮动按钮</p>',
    '<img src="./local.png">', '<a href="javascript:run()">点击</a>',
    '<img src="https://example.com/one.png" data-src="https://example.com/two.png">',
    '<section tn-cell-type="group" tn-bind-cell="col1">未展开组件</section>',
])
def test_unsupported_content_does_not_silently_become_successful_plain_text(content):
    with pytest.raises(ValueError):
        prepare_clipboard_export(content)


def test_budget_failure_does_not_truncate_body():
    source = '<p>' + '完整正文' * 5100 + '</p>'
    with pytest.raises(ValueError, match='budget'):
        prepare_clipboard_export(source)
