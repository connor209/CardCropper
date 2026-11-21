// Clicking the toolbar button shows or hides the panel on a CardUploader tab.
chrome.action.onClicked.addListener((tab) => {
  if (!tab.id || !/^https:\/\/(www\.)?carduploader\.com\//.test(tab.url || "")) return;
  chrome.tabs.sendMessage(tab.id, { type: "cc-toggle" }).catch(() => {});
});
