import {test, expect} from '@playwright/test';

const origin=process.env.KOPEYKA_TEST_URL || 'http://127.0.0.1:8502';
const entries=Array.from({length:40},(_,i)=>({session:`test-${i}`,contract:`24/03/${i+1}-01`,created:'2026-11-14T09:30:00Z',status:'Завершена'}));

for(const viewport of [{width:1440,height:900},{width:390,height:844},{width:844,height:390}]) {
  test(`history stays centered and scrollable at ${viewport.width}x${viewport.height}`,async({page})=>{
    await page.setViewportSize(viewport);
    await page.route('**/api/history',route=>route.fulfill({json:entries}));
    await page.goto(origin);
    const trigger=page.getByRole('button',{name:'История сверок',exact:true});
    await trigger.click();
    const modal=page.getByRole('dialog',{name:'История сверок',exact:true});
    await expect(modal.locator('.history-entry')).toHaveCount(40);
    const box=await modal.boundingBox();
    expect(Math.abs(box.x+box.width/2-viewport.width/2)).toBeLessThan(2);
    expect(Math.abs(box.y+box.height/2-viewport.height/2)).toBeLessThan(2);
    expect(box.y).toBeGreaterThanOrEqual(10);
    expect(box.y+box.height).toBeLessThanOrEqual(viewport.height-10);
    expect(await modal.locator('.history-list').evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
    await expect(modal.getByRole('button',{name:'Закрыть',exact:true})).toBeInViewport();
    await modal.locator('.history-entry').last().scrollIntoViewIfNeeded();
    await expect(modal.getByRole('button',{name:'Закрыть историю',exact:true})).toBeInViewport();
    await page.screenshot({path:`../outputs/history-${viewport.width}.png`});
    await page.keyboard.press('Escape');
    await expect(modal).not.toBeVisible();
    await expect(trigger).toBeFocused();
  });
}

test('empty, error and backdrop dismissal stay inside the modal',async({page})=>{
  await page.route('**/api/history',route=>route.fulfill({json:[]}));
  await page.goto(origin);
  await page.getByRole('button',{name:'История сверок',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'История сверок',exact:true});
  await expect(modal.getByText('Здесь появятся ваши сверки')).toBeVisible();
  await page.mouse.click(2,2);
  await expect(modal).not.toBeVisible();
  await page.unroute('**/api/history');
  await page.route('**/api/history',route=>route.fulfill({status:500,json:{detail:'failed'}}));
  await page.getByRole('button',{name:'История сверок',exact:true}).click();
  await expect(modal.getByRole('alert')).toHaveText('История недоступна');
});
