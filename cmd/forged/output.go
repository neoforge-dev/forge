//go:build !tmux_bridge
// +build !tmux_bridge

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// OutputFormat represents the supported output formats for API/CLI responses.
type OutputFormat string

const (
	OutputFormatJSON  OutputFormat = "json"
	OutputFormatYAML  OutputFormat = "yaml"
	OutputFormatTable OutputFormat = "table"
	OutputFormatPlain OutputFormat = "plain"
)

// OutputFormatter defines the interface implemented by all concrete formatters.
type OutputFormatter interface {
	// Name returns the canonical name for this formatter (json, yaml, table, plain).
	Name() OutputFormat
	// Format writes the given data to w in the formatter's representation.
	Format(w io.Writer, data any) error
}

// ParseOutputFormat normalizes a user-provided format string into a known OutputFormat.
// Defaults to plain when the input is empty or unknown.
func ParseOutputFormat(s string) OutputFormat {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "json":
		return OutputFormatJSON
	case "yaml", "yml":
		return OutputFormatYAML
	case "table":
		return OutputFormatTable
	case "plain", "":
		return OutputFormatPlain
	default:
		return OutputFormatPlain
	}
}

// DetectOutputFormat resolves the effective output format based on a user flag
// and whether the target writer is a TTY or a pipe.
//
// Rules (from CLI design docs):
//   - If flag is provided: honor it.
//   - If no flag:
//   - TTY (interactive): table
//   - Non-TTY (piped/redirection): json
func DetectOutputFormat(flag string, w io.Writer) OutputFormat {
	if strings.TrimSpace(flag) != "" {
		return ParseOutputFormat(flag)
	}

	if isTerminal(w) {
		return OutputFormatTable
	}

	return OutputFormatJSON
}

// WriteOutput renders data to the provided writer using the requested format.
//
// Formats:
//   - json: pretty-printed JSON
//   - yaml: YAML v3
//   - table: simple text table for slices of structs or maps
//   - plain: best-effort human-readable representation
func WriteOutput(w io.Writer, format OutputFormat, data any) error {
	var formatter OutputFormatter

	switch format {
	case OutputFormatJSON:
		formatter = JSONFormatter{}
	case OutputFormatYAML:
		formatter = YAMLFormatter{}
	case OutputFormatTable:
		formatter = TableFormatter{}
	case OutputFormatPlain:
		fallthrough
	default:
		formatter = PlainFormatter{}
	}

	return formatter.Format(w, data)
}

// WriteOutputWithFlag is a convenience helper for CLI commands that accept
// a --format flag. It applies DetectOutputFormat and then writes using
// the resolved format.
func WriteOutputWithFlag(w io.Writer, formatFlag string, data any) error {
	format := DetectOutputFormat(formatFlag, w)
	return WriteOutput(w, format, data)
}

// JSONFormatter renders output as pretty-printed JSON.
type JSONFormatter struct{}

func (JSONFormatter) Name() OutputFormat { return OutputFormatJSON }

func (JSONFormatter) Format(w io.Writer, data any) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(data)
}

// YAMLFormatter renders output as YAML v3.
type YAMLFormatter struct{}

func (YAMLFormatter) Name() OutputFormat { return OutputFormatYAML }

func (YAMLFormatter) Format(w io.Writer, data any) error {
	enc := yaml.NewEncoder(w)
	defer enc.Close()
	return enc.Encode(data)
}

// TableFormatter renders slices of structs or map[string]T as simple text tables.
type TableFormatter struct{}

func (TableFormatter) Name() OutputFormat { return OutputFormatTable }

func (f TableFormatter) Format(w io.Writer, data any) error {
	return writeTable(w, data)
}

// PlainFormatter renders human-readable text, falling back to JSON for complex types.
type PlainFormatter struct{}

func (PlainFormatter) Name() OutputFormat { return OutputFormatPlain }

func (PlainFormatter) Format(w io.Writer, data any) error {
	return writePlain(w, data)
}

func writePlain(w io.Writer, data any) error {
	switch v := data.(type) {
	case nil:
		_, err := fmt.Fprintln(w, "(no data)")
		return err
	case string:
		_, err := fmt.Fprintln(w, v)
		return err
	case []byte:
		_, err := w.Write(v)
		return err
	}

	if s, ok := data.(fmt.Stringer); ok {
		_, err := fmt.Fprintln(w, s.String())
		return err
	}

	// Fallback to JSON for complex types to keep output structured but readable.
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(data)
}

// writeTable attempts to render data as a simple text table.
// Supports:
//   - slices of structs (exported fields only)
//   - slices of map[string]T
//
// Falls back to plain output if the shape is unsupported.
func writeTable(w io.Writer, data any) error {
	v := reflect.ValueOf(data)
	if v.Kind() == reflect.Pointer {
		v = v.Elem()
	}

	if v.Kind() != reflect.Slice && v.Kind() != reflect.Array {
		// Not a collection; fall back to plain rendering.
		return writePlain(w, data)
	}

	if v.Len() == 0 {
		_, err := fmt.Fprintln(w, "(no rows)")
		return err
	}

	first := v.Index(0)
	if first.Kind() == reflect.Pointer {
		first = first.Elem()
	}

	var headers []string
	var rows [][]string

	switch first.Kind() {
	case reflect.Struct:
		// Use exported field names as headers.
		t := first.Type()
		for i := 0; i < t.NumField(); i++ {
			field := t.Field(i)
			if field.PkgPath != "" { // unexported
				continue
			}
			headers = append(headers, field.Name)
		}

		for i := 0; i < v.Len(); i++ {
			rowVal := v.Index(i)
			if rowVal.Kind() == reflect.Pointer {
				rowVal = rowVal.Elem()
			}
			if rowVal.Kind() != reflect.Struct {
				continue
			}

			var row []string
			for _, h := range headers {
				f := rowVal.FieldByName(h)
				if !f.IsValid() {
					row = append(row, "")
					continue
				}
				row = append(row, fmt.Sprint(f.Interface()))
			}
			rows = append(rows, row)
		}

	case reflect.Map:
		// Expect map[string]T; derive headers from union of keys.
		headerSet := map[string]struct{}{}
		for i := 0; i < v.Len(); i++ {
			mv := v.Index(i)
			if mv.Kind() == reflect.Pointer {
				mv = mv.Elem()
			}
			if mv.Kind() != reflect.Map {
				continue
			}
			for _, key := range mv.MapKeys() {
				if key.Kind() == reflect.String {
					headerSet[key.String()] = struct{}{}
				}
			}
		}
		for k := range headerSet {
			headers = append(headers, k)
		}
		sort.Strings(headers)

		for i := 0; i < v.Len(); i++ {
			mv := v.Index(i)
			if mv.Kind() == reflect.Pointer {
				mv = mv.Elem()
			}
			if mv.Kind() != reflect.Map {
				continue
			}

			var row []string
			for _, h := range headers {
				val := mv.MapIndex(reflect.ValueOf(h))
				if !val.IsValid() {
					row = append(row, "")
					continue
				}
				row = append(row, fmt.Sprint(val.Interface()))
			}
			rows = append(rows, row)
		}

	default:
		// Unsupported element type.
		return writePlain(w, data)
	}

	if len(headers) == 0 {
		// Nothing meaningful to tabulate.
		return writePlain(w, data)
	}

	// Compute column widths.
	colWidths := make([]int, len(headers))
	for i, h := range headers {
		colWidths[i] = len(h)
	}
	for _, row := range rows {
		for i, cell := range row {
			if len(cell) > colWidths[i] {
				colWidths[i] = len(cell)
			}
		}
	}

	// Helper to print a row with padding.
	printRow := func(cols []string) {
		for i, col := range cols {
			if i > 0 {
				fmt.Fprint(w, "  ")
			}
			fmt.Fprintf(w, "%-*s", colWidths[i], col)
		}
		fmt.Fprintln(w)
	}

	// Header.
	printRow(headers)

	// Separator.
	sep := make([]string, len(headers))
	for i := range headers {
		sep[i] = strings.Repeat("-", colWidths[i])
	}
	printRow(sep)

	// Rows.
	for _, row := range rows {
		printRow(row)
	}

	return nil
}

// isTerminal detects whether the given writer is backed by a TTY.
// It is intentionally minimal to avoid extra dependencies.
func isTerminal(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	info, err := f.Stat()
	if err != nil {
		return false
	}
	return (info.Mode() & os.ModeCharDevice) != 0
}
