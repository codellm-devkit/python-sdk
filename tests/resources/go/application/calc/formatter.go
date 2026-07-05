// Second file in the calc package.
// Exercises: multi-file package, cross-file method (FormatResult is a free function,
// Describe is a value-receiver method on Calculator defined here while Calculator
// is declared in calc.go — exercises reconcileCrossFileMethods).
package calc

import "strings"

// Describe returns a human-readable description.
// Exercises: value receiver on a type from a sibling file (cross-file method).
func (c Calculator) Describe() string {
	return "Calculator(" + c.label + ")"
}

// FormatResult formats a numeric result with a tag.
// Exercises: variadic — tags ...string — is_variadic=true.
func FormatResult(value int, tags ...string) string {
	return strings.Join(tags, ",") + ":" + strings.TrimSpace(strings.Repeat(" ", value))
}
