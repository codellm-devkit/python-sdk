/**
 * (C) Copyright IBM Corporation 2019.
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

import javax.enterprise.util.AnnotationLiteral;

import com.ibm.websphere.samples.daytrader.interfaces.RuntimeMode;

public class TradeRunTimeModeLiteral extends AnnotationLiteral<RuntimeMode> implements RuntimeMode {
  
  private static final long serialVersionUID = -252789556335033400L;
    private String value;
    public TradeRunTimeModeLiteral(String value) {
        this.value = value;
    }

    @Override
    public String value() {
         return value;
    }

}