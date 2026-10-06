# Homebrew's formula for browserd, from this repository as a tap:
#
#     brew tap jjenkins2004/browserd https://github.com/jjenkins2004/browserd
#     brew install browserd
#
# scripts/release sets url and sha256 for each release; brew install --HEAD builds main.
class Browserd < Formula
  desc "Browser MCP server that gives Claude Code agents Chrome tabs of their own"
  homepage "https://github.com/jjenkins2004/browserd"
  url "https://github.com/jjenkins2004/browserd/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  head "https://github.com/jjenkins2004/browserd.git", branch: "main"

  depends_on :macos
  depends_on "node"
  depends_on "python@3.13"

  def install
    # A --HEAD build is a git clone, which .gitattributes' export-ignore does not reach.
    rm_r ["browser/dashboard/preview", *Dir["browser/dashboard/ui/{*.stories.js,samples.js}"]] if build.head?
    libexec.install "browser", "browserd", "package.json", "package-lock.json", "VERSION"
    cd libexec do
      system "npm", "ci", "--omit=dev", "--no-audit", "--no-fund"
    end
    # Its records go to ~/Library/Application Support/browserd, never the Cellar, which an upgrade replaces.
    (bin/"browserd").write_env_script libexec/"browserd", BROWSERD_PYTHON: Formula["python@3.13"].opt_bin/"python3.13"
  end

  def caveats
    <<~EOS
      browserd needs Google Chrome:
        brew install --cask google-chrome

      To connect Claude Code, run:
        claude mcp add --scope user browserd -- browserd mcp
      browserd then starts whenever an agent needs it; browserd setup changes its ports.

      After brew upgrade browserd, run browserd restart: every Chrome and session is kept.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/browserd version") unless build.head?
    ENV["BROWSERD_HOME"] = testpath/"records"
    assert_match "running", shell_output("#{bin}/browserd status")
  end
end
