// Package calc provides arithmetic operations.
// Exercises: struct with exported/unexported fields, exported/unexported methods,
// pointer receiver, multiple return types (T, error), interface, is_embedded.
package calc

import "fmt"

// Operator is the arithmetic interface.
// Exercises: is_interface=true.
type Operator interface {
	Add(a, b int) (int, error)
}

// base holds common state shared by embedding.
// Exercises: embedded struct field (is_embedded=true in Calculator).
type base struct {
	label string
}

// Calculator implements Operator and embeds base.
type Calculator struct {
	base           // embedded — exercises is_embedded=true
	precision int  // unexported field
}

// New constructs a Calculator.
// Exercises: constructor-style function, single return.
func New(label string) *Calculator {
	return &Calculator{base: base{label: label}, precision: 2}
}

// Add performs integer addition.
// Exercises: pointer receiver, multiple return types (int, error).
func (c *Calculator) Add(a, b int) (int, error) {
	if c == nil {
		return 0, fmt.Errorf("nil calculator")
	}
	return a + b, nil
}

// Label returns the calculator's label.
// Exercises: pointer receiver, single return type.
func (c *Calculator) Label() string {
	return c.label
}

// precision returns the internal precision value.
// Exercises: unexported method (is_exported=false).
func (c *Calculator) precisionValue() int {
	return c.precision
}
