import ArchivePage from "./pages/ArchivePage";
import PrivacyPolicyPage from "./pages/PrivacyPolicyPage";
import TermsPage from "./pages/TermsPage";
import "./styles/tokens.css";
import "./App.css";
import "./styles/v2.css";
import "./styles/phase2a.css";
import "./styles/phase2b-home.css";
import "./styles/phase2c-insights-themes.css";

function App() {
  const pathname = window.location.pathname.replace(/\/+$/, "") || "/";

  if (pathname === "/privacy") {
    return <PrivacyPolicyPage />;
  }

  if (pathname === "/terms") {
    return <TermsPage />;
  }

  return <ArchivePage />;
}

export default App;
