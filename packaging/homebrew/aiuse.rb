class Aiuse < Formula
  include Language::Python::Virtualenv

  desc "Aggregate AI subscription quotas and flag use-it-or-lose-it allotments"
  homepage "https://github.com/djbclark/aiuse"
  url "https://github.com/djbclark/aiuse/archive/refs/tags/v3.0.30.tar.gz"
  sha256 "a20b1566f65b8aa36f789ac10dc0c2775401a9dfc7bc05fe31133f788d9cba24"
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
