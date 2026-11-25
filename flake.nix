{
  description = "aiuse: aggregate AI subscription/API usage and flag use-it-or-lose-it allotments";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      # Single source of truth for the version: pyproject.toml.
      version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;
    in
    {
      packages = forAllSystems (pkgs: rec {
        aiuse = pkgs.python3Packages.buildPythonApplication {
          pname = "aiuse";
          inherit version;
          pyproject = true;
          # Flake sources are the git-tracked tree; tests read docs/ and packaging/.
          src = ./.;
          nativeBuildInputs = [ pkgs.installShellFiles ];
          postInstall = ''
            installShellCompletion --bash --name aiuse completions/aiuse.bash
            installShellCompletion --bash --name ai completions/ai.bash
            installShellCompletion --zsh --name _aiuse completions/aiuse.zsh
            installShellCompletion --zsh --name _ai completions/ai.zsh
          '';
          build-system = with pkgs.python3Packages; [
            setuptools
            wheel
          ];
          dependencies = with pkgs.python3Packages; [
            pyyaml
            requests
            rich
          ];
          optional-dependencies.chrome-refresh = [ pkgs.python3Packages.browser-cookie3 ];
          pythonImportsCheck = [ "aiuse" ];
          nativeCheckInputs = [
            pkgs.python3Packages.pytestCheckHook
            pkgs.python3Packages.jsonschema
            pkgs.bash
            pkgs.git
          ];
          # These run repo shell scripts (not shipped in the package) under a
          # scrubbed PATH / #!/usr/bin/env shebang that the Nix sandbox lacks.
          disabledTests = [
            "test_install_deps_check_accepts_supported_set_without_deprecated_tools"
            "test_install_deps_check_reports_a_missing_supported_tool"
            "test_launchd_refresh_wrapper_runs_both_pools_even_when_first_fails"
          ];
          preCheck = ''
            export HOME=$(mktemp -d)
          '';
          postInstallCheck = ''
            $out/bin/aiuse --version | grep -F "${version}"
            $out/bin/ai --version | grep -F "${version}"
          '';
          meta = {
            description = "Aggregate AI subscription/API usage and flag use-it-or-lose-it allotments";
            homepage = "https://github.com/djbclark/aiuse";
            changelog = "https://github.com/djbclark/aiuse/blob/v${version}/CHANGELOG.md";
            license = pkgs.lib.licenses.mit;
            mainProgram = "aiuse";
            platforms = pkgs.lib.platforms.unix;
          };
        };
        default = aiuse;
      });

      apps = forAllSystems (pkgs: rec {
        aiuse = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.aiuse}/bin/aiuse";
          meta.description = "Run aiuse";
        };
        default = aiuse;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.python3
            pkgs.uv
            pkgs.just
            pkgs.bun
          ];
          shellHook = ''
            echo "aiuse dev shell: run 'uv sync --extra dev' then 'just test'"
          '';
        };
      });

      # `nix flake check` builds the package, which runs the test suite.
      checks = forAllSystems (pkgs: {
        aiuse = self.packages.${pkgs.stdenv.hostPlatform.system}.aiuse;
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
