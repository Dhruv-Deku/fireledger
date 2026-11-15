/** sessionStorage key: the studio intro plays once per visit */
export const INTRO_KEY = "fl-intro-seen";

/** runs in <head>: later loads in the same visit hide the intro before paint */
export const INTRO_INIT_SCRIPT = `try{if(sessionStorage.getItem("${INTRO_KEY}")==="1")document.documentElement.setAttribute("data-intro-seen","")}catch(e){}`;
