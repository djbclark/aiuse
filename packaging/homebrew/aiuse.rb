class Aiuse < Formula
  include Language::Python::Virtualenv

  desc "Aggregate AI subscription quotas and flag use-it-or-lose-it allotments"
  homepage "https://github.com/djbclark/aiuse"
  url "https://github.com/djbclark/aiuse/archive/refs/tags/v3.0.19.tar.gz"
  sha256 "6ccc58c2e7a2f305f5c5dd6e61fbe1a32edbf05a61b94b22e7ed35c4433cbbc6"
  license "MIT"
  head "https://github.com/djbclark/aiuse.git", branch: "main"

  depends_on "python@3.14"

  def install
    venv = virtualenv_create(libexec, "python3.14")
    venv.pip_install buildpath
    bin.install_symlink libexec/"bin/aiuse"
    bin.install_symlink libexec/"bin/ai"
  end

  def caveats
    <<~EOS
      Data sources (install via packaging/install-deps.sh): cswap, codexbar,
      caut, OpenUsage.ai (app/CLI), OpenUsage.sh, tokscale. Config: ~/.config/aiuse/.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/aiuse --version")
    assert_match version.to_s, shell_output("#{bin}/ai --version")
  end
end
