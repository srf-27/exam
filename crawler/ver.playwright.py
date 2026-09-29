import csv

from playwright.sync_api import sync_playwright
from tqdm import trange

# 详情页只拿图片 URL
BLOCK_TYPES = "**/*.{png,jpg,jpeg,webp,gif,svg,woff,woff2,ttf,otf,mp4}"


def scrape_multi_pages(data, url, pages):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 拿到最大页数
        page = browser.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=60_000)
        page.wait_for_selector('nav.pagination a.pagination__number-link', timeout=5000)
        last_link = page.locator('nav.pagination a.pagination__number-link').last
        MAX_PAGE = int(last_link.inner_text().strip())
        #print(MAX_PAGE)
        page.close()

        # 达到目标页数/超出最大页数就退出
        for page in trange(1, min(pages + 1, MAX_PAGE + 1)):
            current_url = f'{url}/page/{page}/'
            scrape_single_page(browser, data, current_url)
        browser.close()

def scrape_single_page(browser, data, url):
    page = browser.new_page()
    page.goto(url, wait_until='domcontentloaded', timeout=60_000)
    page.wait_for_selector('ul.tease-feed li.tease-feed-item')

    # 一次性批量提取列表字段
    rows = page.evaluate("""() => [...document.querySelectorAll('ul.tease-feed li.tease-feed-item')].map(li => {
        const a = li.querySelector('a.post-tease__h__link');
        const sub = li.querySelector('span.post-tease__subtitle');
        const author = li.querySelector('li.post-tease__meta-item--author a');
        const date = li.querySelector('li.tease__meta-item--date');
        return {
            url: a ? a.href : '',
            title: (li.querySelector('span.post-tease__title') || {innerText: ''}).innerText,
            subtitle: sub ? sub.innerText.trim() : '',
            author: author ? author.innerText.trim() : '',
            time: date ? date.innerText.trim() : '',
        };
    })""")
    #print(f"{url} 共 {len(rows)} 篇文章")

    # 详情页复用同一个 page，并拦截图片/字体加载
    detail_page = browser.new_page()
    detail_page.route(BLOCK_TYPES, lambda route: route.abort())

    for i, row in enumerate(rows):
        full_title = f"{row['title'].strip()}--{row['subtitle']}" if row['subtitle'] else row['title'].strip()
        author = row['author'].split("\'")[0] #此处要选择要不要去掉届数
        time = row['time']

        # 进具体博客页抓正文、图片、评论数
        detail = scrape_article_detail(detail_page, row['url'])

        data.append({
            'Title': full_title,
            'Author': author,
            'Comment Count': detail['Comment Count'],
            'Time': time,
            'Article Content': detail['Article Content'],
            'Images In Article': detail['Images In Article'],
        })
        #print(f"[{i+1}/{len(rows)}] {full_title} | {author} | {time}")

    detail_page.close()
    page.close()

def scrape_article_detail(page, article_url):
    result = {'Article Content': '', 'Images In Article': '', 'Comment Count': 0}
    try:
        page.goto(article_url, wait_until='domcontentloaded', timeout=60_000)
        page.wait_for_selector('div.article__body', timeout=10_000)
        result['Article Content'] = page.locator('div.article__body').inner_text()
        result['Images In Article'] = '\n'.join(
            page.locator('div.article__body img').evaluate_all(
                "els => els.map(e => e.getAttribute('data-flickity-lazyload-src') || e.src).filter(Boolean)"
            )
        )
    except Exception as e:
        print(f"[详情抓取失败] {article_url} -> {e}")
    return result


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
    url = "https://mitadmissions.org/blogs/"
    data = []
    scrape_multi_pages(data, url, pages=1)
    save_data_to_csv(data, filename='data_playwright.csv')
    print(f"共 {len(data)} 条，已保存到 data_playwright.csv")
