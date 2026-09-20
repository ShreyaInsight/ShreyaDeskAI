import { test, expect } from '@playwright/test'

test('mobile layout: avatar in header, help icon removed, no spacious margins', async ({ page }, info) => {
  // Verify layout structure by inspecting DOM and computed styles
  // Inspect the dashboard rather than the unauthenticated login screen.
  await page.route('**/api/session', r => r.fulfill({json:{connected:true}}))

  await page.request.post('/api/login',{data:{username:'fixture-user',password:'fixture-password'}})
 await page.goto('/')

  if (info.project.name === 'mobile') {
    // On mobile (iPhone 13 ~390px width)
    await expect(page).toHaveURL('/')

    // Check that mobile header container exists and has proper structure
    const mobileHeaderActions = page.locator('.mobile-header-actions')
    await expect(mobileHeaderActions).toBeVisible()

    // Avatar should be inside mobile-header-actions
    const avatar = mobileHeaderActions.locator('.avatar')
    await expect(avatar).toBeVisible()

    // Menu button should be in mobile-header-actions next to avatar
    const menuButton = mobileHeaderActions.locator('.mobile-menu-button')
    await expect(menuButton).toBeVisible()

    // Help icon should NOT exist anywhere
    const helpIcon = page.locator('button[title="Help"]')
    await expect(helpIcon).toHaveCount(0)

    // topbar-actions (desktop-only) should be hidden on mobile
    const topbarActions = page.locator('.topbar-actions')
    const display = await topbarActions.evaluate(el => window.getComputedStyle(el).display)
    expect(display).toBe('none')

    // Verify mobile padding is tighter (check topbar padding)
    const topbar = page.locator('.topbar')
    const padding = await topbar.evaluate(el => window.getComputedStyle(el).padding)
    // Reduced padding: should be 80px 20px or similar, not 100px 20px 25px
    expect(padding).not.toContain('100px 20px')

    // Avatar should be small on mobile (30px)
    const avatarSize = await avatar.boundingBox()
    expect(avatarSize.width).toBeLessThanOrEqual(35) // 30px styled
    expect(avatarSize.height).toBeLessThanOrEqual(35)

    // Take screenshot showing mobile layout
    await page.screenshot({ path: info.outputPath('mobile-login.png'), fullPage: true })
  } else {
    // On desktop (Chrome ~1280px width)
    await expect(page).toHaveURL('/')

    // mobile-header-actions should not be visible/empty on desktop
    const mobileHeaderActions = page.locator('.mobile-header-actions')
    await expect(mobileHeaderActions).toBeHidden()

    // topbar-actions (desktop) should be visible
    const topbarActions = page.locator('.topbar-actions')
    await expect(topbarActions).toBeVisible()

    // Desktop should still have avatar in topbar-actions
    const avatar = topbarActions.locator('.avatar')
    await expect(avatar).toBeVisible()

    // Help icon should NOT exist on any viewport
    const helpIcon = page.locator('button[title="Help"]')
    await expect(helpIcon).toHaveCount(0)

    // Take screenshot showing desktop layout
    await page.screenshot({ path: info.outputPath('desktop-login.png'), fullPage: true })
  }
})
