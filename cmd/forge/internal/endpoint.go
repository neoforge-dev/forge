package internal

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

const DefaultControlPlaneURL = "http://localhost:8081"

// ResolveControlPlaneURL returns the control-plane base URL without the /api suffix.
// Precedence: FORGE_API_URL > ~/.forge/config.toml > project .forge/config.toml >
// project .forge/forge.yaml > built-in default.
func ResolveControlPlaneURL() string {
	if url := strings.TrimSpace(os.Getenv("FORGE_API_URL")); url != "" {
		return normalizeControlPlaneURL(url)
	}

	for _, candidate := range controlPlaneConfigCandidates() {
		if url := readControlPlaneURL(candidate); url != "" {
			return normalizeControlPlaneURL(url)
		}
	}

	return DefaultControlPlaneURL
}

// ResolveAPIBaseURL returns the configured control-plane URL with the /api suffix.
func ResolveAPIBaseURL() string {
	return NormalizeAPIBaseURL(ResolveControlPlaneURL())
}

// NormalizeAPIBaseURL ensures a control-plane URL has the /api suffix exactly once.
func NormalizeAPIBaseURL(raw string) string {
	base := normalizeControlPlaneURL(raw)
	if strings.HasSuffix(base, "/api") {
		return base
	}
	return base + "/api"
}

func normalizeControlPlaneURL(raw string) string {
	url := strings.TrimSpace(raw)
	url = strings.TrimRight(url, "/")
	url = strings.TrimSuffix(url, "/api")
	if url == "" {
		return DefaultControlPlaneURL
	}
	return url
}

func controlPlaneConfigCandidates() []string {
	var candidates []string

	if home, err := os.UserHomeDir(); err == nil && home != "" {
		candidates = append(candidates, filepath.Join(home, ".forge", "config.toml"))
	}

	if cwd, err := os.Getwd(); err == nil && cwd != "" {
		for _, rel := range []string{
			filepath.Join(".forge", "config.toml"),
			filepath.Join(".forge", "forge.yaml"),
		} {
			if path := findUpwardFile(cwd, rel); path != "" {
				candidates = append(candidates, path)
			}
		}
	}

	return uniqueStrings(candidates)
}

func findUpwardFile(startDir, relPath string) string {
	dir := startDir
	for {
		candidate := filepath.Join(dir, relPath)
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}

		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

func readControlPlaneURL(path string) string {
	switch filepath.Ext(path) {
	case ".toml":
		return readControlPlaneURLFromTOML(path)
	case ".yaml", ".yml":
		return readControlPlaneURLFromYAML(path)
	default:
		return ""
	}
}

func readControlPlaneURLFromTOML(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()

	inControlPlane := false
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := trimConfigLine(scanner.Text())
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			inControlPlane = line == "[control_plane]"
			continue
		}

		key, value, ok := splitConfigKV(line, "=")
		if !ok {
			continue
		}
		if inControlPlane && key == "url" {
			return value
		}
		if key == "api_url" {
			return value
		}
	}

	return ""
}

func readControlPlaneURLFromYAML(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()

	inControlPlane := false
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		raw := scanner.Text()
		line := trimConfigLine(raw)
		if line == "" {
			continue
		}

		indent := len(raw) - len(strings.TrimLeft(raw, " "))
		if indent == 0 {
			inControlPlane = false
		}

		if strings.HasPrefix(line, "control_plane:") {
			inControlPlane = true
			continue
		}

		key, value, ok := splitConfigKV(line, ":")
		if !ok {
			continue
		}
		if key == "api_url" {
			return value
		}
		if inControlPlane && key == "url" {
			return value
		}
	}

	return ""
}

func trimConfigLine(line string) string {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" || strings.HasPrefix(trimmed, "#") {
		return ""
	}

	if idx := strings.Index(trimmed, "#"); idx >= 0 {
		trimmed = strings.TrimSpace(trimmed[:idx])
	}
	return trimmed
}

func splitConfigKV(line, sep string) (key, value string, ok bool) {
	parts := strings.SplitN(line, sep, 2)
	if len(parts) != 2 {
		return "", "", false
	}

	key = strings.TrimSpace(parts[0])
	value = strings.Trim(strings.TrimSpace(parts[1]), `"'`)
	if key == "" || value == "" {
		return "", "", false
	}

	return key, value, true
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	out := make([]string, 0, len(values))
	for _, value := range values {
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}
