// The offline page's two behaviours, in a file of their own because the server's content policy
// allows no inline script. Retry opens the family app again; so does the connection returning.
function reopen() {
  location.replace('/family/')
}
document.getElementById('retry').addEventListener('click', reopen)
window.addEventListener('online', reopen)
