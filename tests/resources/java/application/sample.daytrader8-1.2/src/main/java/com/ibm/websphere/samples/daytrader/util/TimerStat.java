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

/**
 *
 * To change this generated comment edit the template variable "typecomment":
 * Window>Preferences>Java>Templates. To enable and disable the creation of type
 * comments go to Window>Preferences>Java>Code Generation.
 */
public class TimerStat {

    private double min = 1000000000.0, max = 0.0, totalTime = 0.0;
    private int count;

    /**
     * Returns the count.
     *
     * @return int
     */
    public int getCount() {
        return count;
    }

    /**
     * Returns the max.
     *
     * @return double
     */
    public double getMax() {
        return max;
    }

    /**
     * Returns the min.
     *
     * @return double
     */
    public double getMin() {
        return min;
    }

    /**
     * Sets the count.
     *
     * @param count
     *            The count to set
     */
    public void setCount(int count) {
        this.count = count;
    }

    /**
     * Sets the max.
     *
     * @param max
     *            The max to set
     */
    public void setMax(double max) {
        this.max = max;
    }

    /**
     * Sets the min.
     *
     * @param min
     *            The min to set
     */
    public void setMin(double min) {
        this.min = min;
    }

    /**
     * Returns the totalTime.
     *
     * @return double
     */
    public double getTotalTime() {
        return totalTime;
    }

    /**
     * Sets the totalTime.
     *
     * @param totalTime
     *            The totalTime to set
     */
    public void setTotalTime(double totalTime) {
        this.totalTime = totalTime;
    }

    /**
     * Returns the max in Secs
     *
     * @return double
     */
    public double getMaxSecs() {
        return max / 1000.0;
    }

    /**
     * Returns the min in Secs
     *
     * @return double
     */
    public double getMinSecs() {
        return min / 1000.0;
    }

    /**
     * Returns the average time in Secs
     *
     * @return double
     */
    public double getAvgSecs() {

        double avg = getTotalTime() / getCount();
        return avg / 1000.0;
    }
}
