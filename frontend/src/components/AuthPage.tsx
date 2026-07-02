import { FormEvent, useMemo, useState } from "react";
import { Eye, EyeOff, LockKeyhole, LogIn, User, UserPlus } from "lucide-react";
import "./AuthPage.css";

type AuthMode = "login" | "register";

interface Props {
  onSubmit: (mode: AuthMode, username: string, password: string) => Promise<void>;
}

const USERNAME_RE = /^[A-Za-z0-9]{5,}$/;
const PASSWORD_RE = /^[\x21-\x7E]+$/;

export default function AuthPage({ onSubmit }: Props) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const usernameOk = USERNAME_RE.test(username);
  const passwordOk = password.length > 8 && PASSWORD_RE.test(password);
  const canSubmit = usernameOk && passwordOk && !busy;

  const helper = useMemo(() => {
    if (username && !usernameOk) return "账号至少 5 位，只能使用数字和字母";
    if (password && !passwordOk) return "密码需超过 8 位，只能使用数字、字母和英文符号";
    return null;
  }, [password, passwordOk, username, usernameOk]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(mode, username.trim(), password);
    } catch (err) {
      setError((err as Error).message || "操作失败");
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: AuthMode) {
    setMode(next);
    setError(null);
  }

  return (
    <main className="auth-screen">
      <form className="auth-panel" onSubmit={submit}>
        <div className="auth-brand">
          <span className="auth-mark">KL</span>
          <div>
            <h1>KnowledgeLab</h1>
            <p>Hybrid Graph + Vector RAG</p>
          </div>
        </div>

        <div className="auth-tabs" role="tablist" aria-label="账号操作">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            className={mode === "login" ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            <LogIn size={15} /> 登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            className={mode === "register" ? "active" : ""}
            onClick={() => switchMode("register")}
          >
            <UserPlus size={15} /> 注册
          </button>
        </div>

        <label className="auth-field">
          <span>账号</span>
          <div className="auth-input">
            <User size={17} />
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value.trim())}
              autoComplete="username"
              placeholder="letter123"
            />
          </div>
        </label>

        <label className="auth-field">
          <span>密码</span>
          <div className="auth-input">
            <LockKeyhole size={17} />
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type={showPassword ? "text" : "password"}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="password123!"
            />
            <button
              type="button"
              className="auth-password-toggle"
              onClick={() => setShowPassword((current) => !current)}
              onMouseDown={(e) => e.preventDefault()}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              title={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
        </label>

        {(helper || error) && <div className={`auth-message ${error ? "error" : ""}`}>{error || helper}</div>}

        <button className="auth-submit" type="submit" disabled={!canSubmit}>
          {mode === "login" ? <LogIn size={17} /> : <UserPlus size={17} />}
          {busy ? "处理中..." : mode === "login" ? "登录" : "创建账号"}
        </button>
      </form>
    </main>
  );
}
