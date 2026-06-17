// Package pipeline runs a sequence of calculation steps concurrently.
// Exercises: goroutine launch (is_goroutine=true), variadic function call,
// struct with unexported fields, interface usage, cyclomatic complexity > 1.
package pipeline

import (
	"sync"

	"example.com/cldk-e2e/calc"
)

// Step describes a single arithmetic operation.
type Step struct {
	Name string `json:"name"`
	A    int    `json:"a"`
	B    int    `json:"b"`
}

// Result holds the outcome of a step.
type Result struct {
	Step  Step `json:"step"`
	Value int  `json:"value"`
	Err   error
}

// Runner executes steps.
// Exercises: interface with a method.
type Runner interface {
	RunAll(steps ...Step) []Result
}

// Pipeline holds an Operator and runs Steps.
type Pipeline struct {
	op      calc.Operator // unexported field
	mu      sync.Mutex
}

// New creates a Pipeline backed by the given Operator.
func New(op calc.Operator) *Pipeline {
	return &Pipeline{op: op}
}

// RunAll executes every Step concurrently and collects Results.
// Exercises: variadic parameter (steps ...Step), goroutine launch (go p.execute),
// cyclomatic_complexity >= 2 (the if err != nil branch in execute).
func (p *Pipeline) RunAll(steps ...Step) []Result {
	results := make([]Result, len(steps))
	var wg sync.WaitGroup
	for i, s := range steps {
		wg.Add(1)
		go p.execute(s, i, results, &wg) // goroutine — is_goroutine=true
	}
	wg.Wait()
	return results
}

// execute processes one step under the mutex.
// Exercises: unexported method (is_exported=false), cyclomatic_complexity >= 2.
func (p *Pipeline) execute(s Step, idx int, results []Result, wg *sync.WaitGroup) {
	defer wg.Done()
	p.mu.Lock()
	defer p.mu.Unlock()
	v, err := p.op.Add(s.A, s.B)
	if err != nil {
		results[idx] = Result{Step: s, Err: err}
		return
	}
	results[idx] = Result{Step: s, Value: v}
}
