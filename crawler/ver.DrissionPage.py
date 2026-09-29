"""
    DrissionPage 多 tab 并发 对比下来比 playwright 快得多
"""
import csv
import queue
from concurrent.futures import ThreadPoolExecutor

from DrissionPage import ChromiumOptions, ChromiumPage
from tqdm import trange

START_URL = "https://mitadmissions.org/blogs/"
PAGES = 10        # 目标页数
WORKERS = 4     # 详情页并发 tab 数，网络好可加到 6~8

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"  # 可删，DrissionPage 可 自动找
USER_DATA = r"dp_profile"  # 独立 profile 目录，避免污染日常浏览器

BLOCK_URLS = ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.svg",
              "*.woff", "*.woff2", "*.ttf", "*.otf", "*.mp4"]

LIST_JS = """
(function () {
  document.querySelectorAll('ul.tease-feed img.wp-smiley, ul.tease-feed img.emoji, ul.tease-feed img[src*="/emoji/"]')
    .forEach(e => e.alt && e.replaceWith(document.createTextNode(e.alt)));
  return [...document.querySelectorAll('ul.tease-feed li.tease-feed-item')].map(li => {
    const a = li.querySelector('a.post-tease__h__link');
    const sub = li.querySelector('span.post-tease__subtitle');
    const author = li.querySelector('li.post-tease__meta-item--author a');
    const date = li.querySelector('li.tease__meta-item--date');
    return {
      url: a ? a.href : '',
      title: (li.querySelector('span.post-tease__title') || {}).innerText || '',
      subtitle: sub ? sub.innerText.trim() : '',
      author: author ? author.innerText.trim() : '',
      time: date ? date.innerText.trim() : '',
    };
  });
})()
"""

# 详情页一次性提取正文 / 图片 / 评论数
DETAIL_JS = """
(function () {
  const body = document.querySelector('div.article__body');
  if (!body) return null;
  body.querySelectorAll('img.wp-smiley, img.emoji, img[src*="/emoji/"]')
    .forEach(e => e.alt && e.replaceWith(document.createTextNode(e.alt)));
  const cc = document.querySelector('.post__comments-count, a[href="#comments"], .comments-count');
  return {
    content: body.innerText,
    images: [...body.querySelectorAll('img')]
      .map(e => e.getAttribute('data-flickity-lazyload-src') || e.src)
      .filter(Boolean),
    comments: cc ? (parseInt(cc.textContent.replace(/\\D/g, '')) || 0) : 0,
  };
})()
"""


def make_browser():
    co = ChromiumOptions()
    if CHROME_PATH:
        co.set_browser_path(CHROME_PATH)
    co.set_local_port(9333)            # 必设，否则 CDP 握手 404
    co.set_user_data_path(USER_DATA)  # 必设，隔离登录态/缓存
    co.headless(True)
    co.set_argument('--no-first-run')
    return ChromiumPage(co)


def setup_tab(tab):
    # CDP 拦截图片字体
    tab.set.load_mode.eager()
    try:
        tab.run_cdp('Network.enable')
        tab.run_cdp('Network.setBlockedURLs', urls=BLOCK_URLS)
    except Exception:
        pass  # 拦截失败不影响主流程


def get_max_page(page, url):
    # 拿最大页数
    page.get(url)
    page.wait.ele_displayed('css:nav.pagination a.pagination__number-link', timeout=10)
    links = page.eles('css:nav.pagination a.pagination__number-link')
    return int(links[-1].text.strip())


def scrape_article_detail(tab, article_url):
    result = {'Article Content': '', 'Images In Article': '', 'Comment Count': 0}
    try:
        tab.get(article_url)
        if tab.wait.ele_displayed('css:div.article__body', timeout=15):
            detail = tab.run_js(DETAIL_JS, as_expr=True)  # as_expr=True 才能回收返回值
            if detail:
                result['Article Content'] = detail['content']
                result['Images In Article'] = ';\n'.join(detail['images'])
                result['Comment Count'] = detail.get('comments', 0)
        else:
            print(f"[详情抓取失败] {article_url} -> 正文未出现")
    except Exception as e:
        print(f"[详情抓取失败] {article_url} -> {e}")
    return result


def worker(tab_pool, article_url):
    tab = tab_pool.get()
    try:
        return scrape_article_detail(tab, article_url)
    finally:
        tab_pool.put(tab)


def scrape_single_page(page, tab_pool, data, url):
    page.get(url)
    page.wait.ele_displayed('css:ul.tease-feed li.tease-feed-item')
    rows = page.run_js(LIST_JS, as_expr=True)
    # print(f"{url} 共 {len(rows)} 篇文章")

    # 详情页多 tab 并发
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        details = list(ex.map(lambda r: worker(tab_pool, r['url']), rows))

    # 回到 Python 侧组装
    for row, detail in zip(rows, details):
        title = row['title'].strip()
        full_title = f"{title}--{row['subtitle']}" if row['subtitle'] else title
        author = row['author'].split("'")[0]  # 此处要选择要不要去掉届数
        data.append({
            'Title': full_title,
            'Author': author,
            'Comment Count': detail['Comment Count'],
            'Time': row['time'],
            'Article Content': detail['Article Content'],
            'Images In Article': detail['Images In Article'],
        })


def scrape_multi_pages(data, url, pages):
    browser = make_browser()
    try:
        browser.set.load_mode.eager()
        max_page = get_max_page(browser, url)

        # 预建详情 tab 池（复用，不再每篇 new/close）
        tab_pool = queue.Queue()
        for _ in range(WORKERS):
            tab = browser.new_tab()
            setup_tab(tab)
            tab_pool.put(tab)

        # 达到目标页数/超出最大页数就退出
        for page_num in trange(1, min(pages + 1, max_page + 1)):
            current_url = f'{url}/page/{page_num}/'
            scrape_single_page(browser, tab_pool, data, current_url)
    finally:
        browser.quit()


def save_data_to_csv(data, filename='data.csv'):
    with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['Title', 'Author', 'Comment Count', 'Time', 'Article Content', 'Images In Article'])
        for row in data:
            writer.writerow([
                row['Title'],
                row['Author'],
                row['Comment Count'],
                row['Time'],
                row['Article Content'],
                row['Images In Article'],
            ])


if __name__ == '__main__':
    data = []
    scrape_multi_pages(data, START_URL, pages=PAGES)
    save_data_to_csv(data,filename='data_DrissionPage.csv')
    print(f"共 {len(data)} 条，已保存到 data_DrissionPage.csv")
