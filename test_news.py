import asyncio
from playwright.async_api import async_playwright
from trafilatura import extract

async def run():
    # 테스트 하고 싶은 뉴스 URL 넣기
    url = "https://news.nate.com/view/20260204n06544?mid=n0105"

    async with async_playwright() as p:
        # 브라우저 실행
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("뉴스를 읽어오는 중...")
        await page.goto(url)

        # 페이지 전체 내용 가져오기
        content = await page.content()

        # 정밀 추출기로 제목과 본문 뽑기
        result = extract(content, output_format='json')

        print("\n=== 추출 결과 ===")
        print(result)

        await browser.close()
if __name__ == "__main__":
    asyncio.run(run())