/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.util;

import java.util.AbstractSequentialList;
import java.util.ListIterator;

public class KeyBlock extends AbstractSequentialList<Object> {

    // min and max provide range of valid primary keys for this KeyBlock
    private int min = 0;
    private int max = 0;
    private int index = 0;

    /**
     * Constructor for KeyBlock
     */
    public KeyBlock() {
        super();
        min = 0;
        max = 0;
        index = min;
    }

    /**
     * Constructor for KeyBlock
     */
    public KeyBlock(int min, int max) {
        super();
        this.min = min;
        this.max = max;
        index = min;
    }

    /**
     * @see AbstractCollection#size()
     */
    @Override
    public int size() {
        return (max - min) + 1;
    }

    /**
     * @see AbstractSequentialList#listIterator(int)
     */
    @Override
    public ListIterator<Object> listIterator(int arg0) {
        return new KeyBlockIterator();
    }

    class KeyBlockIterator implements ListIterator<Object> {

        /**
         * @see ListIterator#hasNext()
         */
        @Override
        public boolean hasNext() {
            return index <= max;
        }

        /**
         * @see ListIterator#next()
         */
        @Override
        public synchronized Object next() {
            if (index > max) {
                throw new java.lang.RuntimeException("KeyBlock:next() -- Error KeyBlock depleted");
            }
            return new Integer(index++);
        }

        /**
         * @see ListIterator#hasPrevious()
         */
        @Override
        public boolean hasPrevious() {
            return index > min;
        }

        /**
         * @see ListIterator#previous()
         */
        @Override
        public Object previous() {
            return new Integer(--index);
        }

        /**
         * @see ListIterator#nextIndex()
         */
        @Override
        public int nextIndex() {
            return index - min;
        }

        /**
         * @see ListIterator#previousIndex()
         */
        @Override
        public int previousIndex() {
            throw new UnsupportedOperationException("KeyBlock: previousIndex() not supported");
        }

        /**
         * @see ListIterator#add()
         */
        @Override
        public void add(Object o) {
            throw new UnsupportedOperationException("KeyBlock: add() not supported");
        }

        /**
         * @see ListIterator#remove()
         */
        @Override
        public void remove() {
            throw new UnsupportedOperationException("KeyBlock: remove() not supported");
        }

        /**
         * @see ListIterator#set(Object)
         */
        @Override
        public void set(Object arg0) {
        }
    }
}