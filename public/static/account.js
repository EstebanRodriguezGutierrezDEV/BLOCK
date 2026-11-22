const dialog = document.querySelector("#delete-account-dialog");
const password = document.querySelector("#delete-account-password");

document.querySelector("#open-delete-account").addEventListener("click", () => dialog.showModal());
document.querySelector("#cancel-delete-account").addEventListener("click", () => dialog.close());
dialog.addEventListener("close", () => { password.value = ""; });
if (dialog.hasAttribute("data-reopen")) dialog.showModal();
