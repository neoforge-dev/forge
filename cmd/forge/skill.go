package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/spf13/cobra"
)

var skillCmd = &cobra.Command{
	Use:   "skill",
	Short: "Discover and inspect Claude Code skills",
	Long:  "List and view skills defined in .claude/skills/.",
}

type skillInfo struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Path        string `json:"path"`
}

var skillListCmd = &cobra.Command{
	Use:   "list",
	Short: "List all available skills",
	RunE: func(cmd *cobra.Command, args []string) error {
		root := findForgeRoot()
		skillsDir := filepath.Join(root, ".claude", "skills")

		entries, err := os.ReadDir(skillsDir)
		if err != nil {
			return fmt.Errorf("cannot read skills directory %s: %w\n\n  Fix: ensure .claude/skills/ exists in your FORGE root", skillsDir, err)
		}

		var skills []skillInfo
		for _, e := range entries {
			if !e.IsDir() {
				continue
			}
			skillFile := filepath.Join(skillsDir, e.Name(), "SKILL.md")
			info := parseSkillFrontmatter(skillFile)
			if info.Name == "" {
				info.Name = e.Name()
			}
			info.Path = filepath.Join(".claude", "skills", e.Name())
			skills = append(skills, info)
		}

		format, _ := cmd.Flags().GetString("format")
		if format == "json" {
			enc := json.NewEncoder(os.Stdout)
			enc.SetIndent("", "  ")
			return enc.Encode(skills)
		}

		if len(skills) == 0 {
			fmt.Println("No skills found in .claude/skills/")
			return nil
		}

		// Table output
		fmt.Printf("%-25s %-60s %s\n", "NAME", "DESCRIPTION", "PATH")
		for _, s := range skills {
			desc := s.Description
			if len(desc) > 58 {
				desc = desc[:55] + "..."
			}
			fmt.Printf("%-25s %-60s %s\n", s.Name, desc, s.Path)
		}
		fmt.Printf("\nTotal: %d skills\n", len(skills))
		return nil
	},
}

var skillShowCmd = &cobra.Command{
	Use:   "show <name>",
	Short: "Show full details for a skill",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		root := findForgeRoot()
		skillFile := filepath.Join(root, ".claude", "skills", args[0], "SKILL.md")

		data, err := os.ReadFile(skillFile)
		if err != nil {
			return fmt.Errorf("skill %q not found\n\n  Expected: %s\n  Fix: run 'forge skill list' to see available skills", args[0], skillFile)
		}

		fmt.Print(string(data))
		return nil
	},
}

func parseSkillFrontmatter(path string) skillInfo {
	f, err := os.Open(path)
	if err != nil {
		return skillInfo{}
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	inFrontmatter := false
	var info skillInfo

	for scanner.Scan() {
		line := scanner.Text()
		if strings.TrimSpace(line) == "---" {
			if inFrontmatter {
				break // end of frontmatter
			}
			inFrontmatter = true
			continue
		}
		if !inFrontmatter {
			continue
		}
		if strings.HasPrefix(line, "name:") {
			info.Name = strings.TrimSpace(strings.TrimPrefix(line, "name:"))
		}
		if strings.HasPrefix(line, "description:") {
			info.Description = strings.TrimSpace(strings.TrimPrefix(line, "description:"))
		}
	}
	return info
}

func init() {
	skillListCmd.Flags().StringP("format", "f", "table", "Output format (table, json)")
	skillCmd.AddCommand(skillListCmd)
	skillCmd.AddCommand(skillShowCmd)
}
