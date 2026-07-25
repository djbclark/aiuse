class Aiuse < Formula
  include Language::Python::Virtualenv

  desc "Aggregate AI subscription quotas and flag use-it-or-lose-it allotments"
  homepage "https://github.com/djbclark/aiuse"
  url "https://github.com/djbclark/aiuse/archive/refs/tags/v2.1.8.tar.gz"
  sha256 "2f02cb20e5afdb7b78185166f6a4899b19e35a06e8604988d15db685592da2c1"
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
      caut, OpenUsage (app and/or CLI), tokscale. Config: ~/.config/aiuse/.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/aiuse --version")
    assert_match version.to_s, shell_output("#{bin}/ai --version")
  end
end
