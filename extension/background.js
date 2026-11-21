// Clicking the toolbar button shows or hides the panel on a CardUploader tab.
//
// Chrome only adds content scripts to pages loaded AFTER the extension was
// installed or reloaded, so a CardUploader tab that was already open has no
// panel to toggle. When the message finds nobody listening, the scripts are
// injected into that tab there and then, and the panel opens.
const CARDUPLOADER = /^https:\/\/([a-z0-9-]+\.)*carduploader\.com\//i;

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;
  if (!CARDUPLOADER.test(tab.url || "")) {
    chrome.action.setBadgeText({ tabId: tab.id, text: "?" });
    chrome.action.setTitle({ tabId: tab.id,
      title: "CardCropper works on carduploader.com — open a batch there first" });
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "cc-toggle" });
  } catch {
    await chrome.scripting.executeScript({ target: { tabId: tab.id },
                                           files: ["lib/push.js", "content.js"] });
    await chrome.tabs.sendMessage(tab.id, { type: "cc-toggle" });
  }
});
