import { LogIn, LogOut, ShieldCheck, UserCircle2 } from "lucide-react";
import type { AuthUser } from "../../auth/cognito";

type AuthStatusProps = {
  user: AuthUser | null;
  isAuthReady: boolean;
  onLogin: () => void;
  onLogout: () => void;
};

export default function AuthStatus({
  user,
  isAuthReady,
  onLogin,
  onLogout,
}: AuthStatusProps) {
  return (
    <section className="auth-status-card">
      <div className="auth-status-icon">
        {user ? <ShieldCheck size={18} /> : <UserCircle2 size={18} />}
      </div>

      <div className="auth-status-copy">
        <strong>{user ? "Signed in" : "Demo mode"}</strong>
        <span>
          {user?.email || (isAuthReady ? "Using demo-user archive" : "Checking session...")}
        </span>
      </div>

      {user ? (
        <button onClick={onLogout}>
          <LogOut size={16} />
          Logout
        </button>
      ) : (
        <button onClick={onLogin}>
          <LogIn size={16} />
          Login
        </button>
      )}
    </section>
  );
}
