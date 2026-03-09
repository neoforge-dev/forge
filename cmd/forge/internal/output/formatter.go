// Package output provides formatting capabilities for CLI output.
// Supports table, JSON, CSV, and quiet formats.
package output

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"strings"
	"text/tabwriter"
)

// Formatter formats data for output
type Formatter interface {
	Format(data interface{}) error
}

// New creates a formatter based on the format name
func New(format string, w io.Writer) (Formatter, error) {
	switch format {
	case "json":
		return &JSONFormatter{w: w}, nil
	case "csv":
		return &CSVFormatter{w: w}, nil
	case "table":
		return &TableFormatter{w: w}, nil
	case "quiet":
		return &QuietFormatter{}, nil
	default:
		return nil, fmt.Errorf("unknown format: %s", format)
	}
}

// AutoFormat returns the best format for the current environment
func AutoFormat() string {
	if !isTTY() {
		return "json"
	}
	return "table"
}

func isTTY() bool {
	fileInfo, _ := os.Stdout.Stat()
	return (fileInfo.Mode() & os.ModeCharDevice) != 0
}

// JSONFormatter outputs data as JSON
type JSONFormatter struct {
	w io.Writer
}

func (f *JSONFormatter) Format(data interface{}) error {
	encoder := json.NewEncoder(f.w)
	encoder.SetIndent("", "  ")
	return encoder.Encode(data)
}

// CSVFormatter outputs data as CSV
type CSVFormatter struct {
	w io.Writer
}

func (f *CSVFormatter) Format(data interface{}) error {
	// Convert data to CSV
	// This is a simplified implementation
	writer := csv.NewWriter(f.w)
	defer writer.Flush()

	// Handle slice of structs
	v := reflect.ValueOf(data)
	if v.Kind() == reflect.Slice && v.Len() > 0 {
		elem := v.Index(0)
		if elem.Kind() == reflect.Struct {
			// Write header
			t := elem.Type()
			header := make([]string, t.NumField())
			for i := 0; i < t.NumField(); i++ {
				header[i] = strings.ToLower(t.Field(i).Name)
			}
			writer.Write(header)

			// Write rows
			for i := 0; i < v.Len(); i++ {
				row := make([]string, t.NumField())
				elem := v.Index(i)
				for j := 0; j < t.NumField(); j++ {
					row[j] = fmt.Sprintf("%v", elem.Field(j).Interface())
				}
				writer.Write(row)
			}
		}
	}

	return nil
}

// TableFormatter outputs data as a formatted table
type TableFormatter struct {
	w io.Writer
}

func (f *TableFormatter) Format(data interface{}) error {
	// Use tabwriter for aligned columns
	tw := tabwriter.NewWriter(f.w, 0, 0, 2, ' ', 0)
	defer tw.Flush()

	// Simplified table output
	fmt.Fprintf(tw, "Output:\t%s\n", data)
	return nil
}

// QuietFormatter outputs nothing (exit codes only)
type QuietFormatter struct{}

func (f *QuietFormatter) Format(data interface{}) error {
	return nil
}
