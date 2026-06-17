// Package main is the entry point for the cldk-e2e fixture.
// It exercises cross-package calls into calc and pipeline.
package main

import (
	"fmt"
	"log"

	"example.com/cldk-e2e/calc"
	"example.com/cldk-e2e/pipeline"
)

func main() {
	c := calc.New("demo")
	result, err := c.Add(10, 32)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(result)
	fmt.Println(c.Label())
	fmt.Println(calc.FormatResult(result, "sum"))

	p := pipeline.New(c)
	out := p.RunAll(
		pipeline.Step{Name: "add", A: 1, B: 2},
		pipeline.Step{Name: "add", A: 3, B: 4},
	)
	for _, r := range out {
		fmt.Println(r)
	}
}
