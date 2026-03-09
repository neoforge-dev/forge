//go:build !private
// +build !private

package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

var selfUpdateCmd = &cobra.Command{
	Use:   "self-update",
	Short: "Check for updates",
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println("See releases at https://github.com/neoforge-dev/forge/releases")
		return nil
	},
}
