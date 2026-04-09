package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

// advancedCmd is an umbrella that makes all hidden commands discoverable.
// Power users can run `forge advanced --help` to see every command.
// Setting FORGE_SHOW_ALL=1 also un-hides everything.
var advancedCmd = &cobra.Command{
	Use:   "advanced",
	Short: "Show all advanced and internal commands",
	Long: `All FORGE commands, including internal and low-frequency operations.

These are real commands — hidden from root help to reduce noise for new users.

To permanently unhide all commands, set FORGE_SHOW_ALL=1:
  FORGE_SHOW_ALL=1 forge --help`,
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Println("Advanced FORGE commands (all are real and functional):")
		fmt.Println()
		for _, c := range rootCmd.Commands() {
			if c.Hidden {
				fmt.Printf("  %-20s %s\n", c.Name(), c.Short)
			}
		}
		fmt.Println()
		fmt.Println("Run 'forge <command> --help' for details on any command.")
		fmt.Println("Run 'FORGE_SHOW_ALL=1 forge --help' to show all in root help.")
	},
}

func init() {
	advancedCmd.Hidden = true
	rootCmd.AddCommand(advancedCmd)
}
