/**
 * (C) Copyright IBM Corporation 2016.
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
package com.ibm.websphere.samples.daytrader.web.prims;

/**
 * Simple bean to get and set messages
 */

public class PingBean {

    private String msg;

    /**
     * returns the message contained in the bean
     *
     * @return message String
     **/
    public String getMsg() {
        return msg;
    }

    /**
     * sets the message contained in the bean param message String
     **/
    public void setMsg(String s) {
        msg = s;
    }
}